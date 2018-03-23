#######################################################################
# Copyright (C) 2017 Shangtong Zhang(zhangshangtong.cpp@gmail.com)    #
# Permission given to modify the code as long as you keep this        #
# declaration at the top                                              #
#######################################################################

import torch
from torch.autograd import Variable
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
import pickle as pickle
import torchvision
from skimage import io
from collections import deque
import gym
import torch.optim
from utils import *
from tqdm import tqdm
from network import *

# PREFIX = '.'
PREFIX = 'data'
torch.set_default_tensor_type('torch.FloatTensor')

class Network(nn.Module, BasicNet):
    def __init__(self, num_actions, twoway = False, gpu=0):
        super(Network, self).__init__()
        self.twoway = twoway
        self.conv1 = nn.Conv2d(12, 64, 8, 2, (0, 1))
        self.conv2 = nn.Conv2d(64, 128, 6, 2, (1, 1))
        self.conv3 = nn.Conv2d(128, 128, 6, 2, (1, 1))
        self.conv4 = nn.Conv2d(128, 128, 4, 2, (0, 0))


        self.hidden_units = 128 * 11 * 8

        self.fc5 = nn.Linear(self.hidden_units, 2048)
        self.fc_encode = nn.Linear(2048, 2048)
        self.fc_action = nn.Linear(num_actions, 2048)
        if self.twoway :
            self.fc_decode = nn.Linear(4096, 2048)
        else :
            self.fc_decode = nn.Linear(2048, 2048)
        self.fc8 = nn.Linear(2048, self.hidden_units)

        self.deconv9 = nn.ConvTranspose2d(128, 128, 4, 2)
        self.deconv10 = nn.ConvTranspose2d(128, 128, 6, 2, (1, 1))
        self.deconv11 = nn.ConvTranspose2d(128, 128, 6, 2, (1, 1))
        self.deconv12 = nn.ConvTranspose2d(128, 3, 8, 2, (0, 1))

        self.init_weights()
        self.criterion = nn.MSELoss()
        self.opt = torch.optim.Adam(self.parameters(), 1e-4)

        BasicNet.__init__(self, gpu)

    def init_weights(self):
        for layer in self.children():
            if isinstance(layer, nn.Conv2d) or isinstance(layer, nn.ConvTranspose2d):
                nn.init.xavier_uniform(layer.weight.data)
            # nn.init.constant(layer.bias.data, 0)
        nn.init.uniform(self.fc_encode.weight.data, -1, 1)
        nn.init.uniform(self.fc_decode.weight.data, -1, 1)
        nn.init.uniform(self.fc_action.weight.data, -0.1, 0.1)

    def forward(self, obs, action, gen_iact=False, gen_iall=False):
        x = F.relu(self.conv1(obs))
        x = F.relu(self.conv2(x))
        x = F.relu(self.conv3(x))
        x = F.relu(self.conv4(x))
        x = x.view((-1, self.hidden_units))
        x = F.relu(self.fc5(x))
        action = self.fc_action(action)
        if self.twoway :
            y = self.fc_encode(x)
            y_a = torch.mul(y, action)
            if not gen_iact and not gen_iall :
                # define x as an y, and define action lat_vec as y_a
                x = torch.cat((y,y_a), 1) #concat of y and y_a
            elif gen_iact :
                dummy_vec = torch.autograd.Variable(torch.zeros_like(y_a.data))
                x = torch.cat((dummy_vec,y_a), 1) #concat of 00000 and y_a
            else :
                dummy_vec = torch.autograd.Variable(torch.zeros_like(y_a.data))
                x = torch.cat((y, dummy_vec), 1)  # concat of 00000 and y
        else :
            x = self.fc_encode(x)
            x = torch.mul(x, action)
        x = self.fc_decode(x)
        x = F.relu(self.fc8(x))
        x = x.view((-1, 128, 11, 8))
        x = F.relu(self.deconv9(x))
        x = F.relu(self.deconv10(x))
        x = F.relu(self.deconv11(x))
        x = self.deconv12(x)
        return x

    def fit(self, x, a, y):
        x = self.variable(x)
        a = self.variable(a)
        y = self.variable(y)
        y_ = self.forward(x, a)
        loss = self.criterion(y_, y)
        self.opt.zero_grad()
        loss.backward()
        for param in self.parameters():
            param.grad.data.clamp_(-0.1, 0.1)
        self.opt.step()
        return np.asscalar(loss.cpu().data.numpy())

    def evaluate(self, x, a, y):
        x = self.variable(x)
        a = self.variable(a)
        y = self.variable(y)
        y_ = self.forward(x, a)
        loss = self.criterion(y_, y)
        return np.asscalar(loss.cpu().data.numpy())

    def predict(self, x, a):
        x = self.variable(x)
        a = self.variable(a)
        return self.forward(x, a).cpu().data.numpy()

    def gen_iactNiall(self, x, a):
        x = self.variable(x)
        a = self.variable(a)
        i_action = self.forward(x, a, gen_iact=True).cpu().data.numpy()
        i_others = self.forward(x, a, gen_iall=True).cpu().data.numpy()
        return i_action, i_others


def load_episode(game, ep, num_actions):
    path = '%s/dataset/%s/%05d' % (PREFIX, game, ep)
    with open('%s/action.bin' % (path), 'rb') as f:
        actions = pickle.load(f)
    num_frames = len(actions) + 1
    frames = []

    for i in range(1, num_frames):
        frame = io.imread('%s/%05d.png' % (path, i))
        frame = np.transpose(frame, (2, 0, 1))
        frames.append(frame.astype(np.uint8))

    actions = actions[1:]
    encoded_actions = np.zeros((len(actions), num_actions))
    encoded_actions[np.arange(len(actions)), actions] = 1

    return frames, encoded_actions

def extend_frames(frames, actions):
    buffer = deque(maxlen=4)
    extended_frames = []
    targets = []

    for i in range(len(frames) - 1):
        buffer.append(frames[i])
        if len(buffer) >= 4:
            extended_frames.append(np.vstack(buffer))
            targets.append(frames[i + 1])
    actions = actions[3:, :]

    return np.stack(extended_frames), actions, np.stack(targets)

def train(game, twoway = False):
    env = gym.make(game)
    num_actions = env.action_space.n

    net = Network(num_actions, twoway = twoway)

    with open('%s/dataset/%s/meta.bin' % (PREFIX, game), 'rb') as f:
        meta = pickle.load(f)
    episodes = meta['episodes']
    mean_obs = meta['mean_obs']

    def pre_process(x):
        if x.shape[1] == 12:
            return (x - np.vstack([mean_obs] * 4)) / 255.0
        elif x.shape[1] == 3:
            return (x - mean_obs) / 255.0
        else:
            assert False

    def post_process(y):
        return (y * 255 + mean_obs).astype(np.uint8)

    train_episodes = int(episodes * 0.95)
    indices_train = np.arange(train_episodes)
    iteration = 0
    while True:
        np.random.shuffle(indices_train)
        for ep in indices_train:
            frames, actions = load_episode(game, ep, num_actions)
            frames, actions, targets = extend_frames(frames, actions)
            batcher = Batcher(32, [frames, actions, targets])
            batcher.shuffle()
            while not batcher.end():
                if iteration % 10000 == 0:
                    mkdir('/data/acvp-sample')
                    losses = []
                    test_indices = range(train_episodes, episodes)
                    ep_to_print = np.random.choice(test_indices)
                    for test_ep in tqdm(test_indices):
                        frames, actions = load_episode(game, test_ep, num_actions)
                        frames, actions, targets = extend_frames(frames, actions)
                        test_batcher = Batcher(32, [frames, actions, targets])
                        while not test_batcher.end():
                            x, a, y = test_batcher.next_batch()
                            losses.append(net.evaluate(pre_process(x), a, pre_process(y)))
                        # if test_ep == ep_to_print:
                        if iteration % 100 == 0 :
                            test_batcher.reset()
                            x, a, y = test_batcher.next_batch()
                            y_ = post_process(net.predict(pre_process(x), a))
                            directory = 'data/acvp-sample/%s' % game
                            if not os.path.exists(directory):
                                os.makedirs(directory)
                            if twoway :
                                y_action, y_others = net.gen_iactNiall(pre_process(x), a)
                                torchvision.utils.save_image(torch.from_numpy(post_process(y_action)),
                                                             'data/acvp-sample/%s/%d-action.png' % (game, iteration))
                                torchvision.utils.save_image(torch.from_numpy(post_process(y_others)),
                                                             'data/acvp-sample/%s/%d-others.png' % (game, iteration))
                            torchvision.utils.save_image(torch.from_numpy(y_), 'data/acvp-sample/%s/%d.png' % (game, iteration))
                            torchvision.utils.save_image(torch.from_numpy(y), 'data/acvp-sample/%s/%d-truth.png' % (game, iteration))

                    logger.info('Iteration %d, test loss %f' % (iteration, np.mean(losses)))
                    torch.save(net.state_dict(), 'data/acvp-%s.bin' % (game))

                x, a, y = batcher.next_batch()
                loss = net.fit(pre_process(x), a, pre_process(y))
                if iteration % 100 == 0:
                    logger.info('Iteration %d, loss %f' % (iteration, loss))
                iteration += 1
