import theano
from theano import tensor as T
import lasagne
from lasagne.layers import *
from lasagne.objectives import *
from lasagne.nonlinearities import *
from lasagne.updates import *
from lasagne.utils import *
import numpy as np
import cPickle as pickle
import gzip
import matplotlib
matplotlib.use('agg')
import matplotlib.pyplot as plt
from matplotlib import pyplot
import os
import sys
from time import time
sys.path.append("..")
import common
from psutil import virtual_memory
from pylab import rcParams
rcParams['figure.figsize'] = 15, 20

# -------------------------------------

def get_net(net_cfg, args):
    l_out = net_cfg(args)
    X = T.tensor4('X')
    net_out = get_output(l_out, X)
    loss = squared_error(net_out, X).mean()
    params = get_all_params(l_out, trainable=True)
    lr = theano.shared(floatX(args["learning_rate"]))
    if "rmsprop" in args:
        updates = rmsprop(loss, params, learning_rate=lr)
    else:
        updates = nesterov_momentum(loss, params, learning_rate=lr, momentum=0.9)
    #updates = adadelta(loss, params, learning_rate=lr)
    #updates = rmsprop(loss, params, learning_rate=lr)
    train_fn = theano.function([X], loss, updates=updates)
    loss_fn = theano.function([X], loss)
    out_fn = theano.function([X], net_out)
    return {
        "train_fn": train_fn,
        "loss_fn": loss_fn,
        "out_fn": out_fn,
        "lr": lr,
        "l_out": l_out
    }


def autoencoder_basic(args={"f":32, "d":4096}):
    conv = InputLayer((None,16,128,128))
    conv = GaussianNoiseLayer(conv, args["sigma"])
    for i in range(0, 4):
        conv = Conv2DLayer(conv, num_filters=(i+1)*args["f"], filter_size=3, nonlinearity=args["nonlinearity"])
        #if i != 3:
        conv = MaxPool2DLayer(conv, pool_size=2)
    # coding layer
    if "d" in args:
        conv = DenseLayer(conv, num_units=args["d"], nonlinearity=args["nonlinearity"])
    for layer in get_all_layers(conv)[::-1]:
        if isinstance(layer, InputLayer):
            break
        conv = InverseLayer(conv, layer)
    for layer in get_all_layers(conv):
        print layer, layer.output_shape
    print count_params(layer)
    return conv


def autoencoder_basic_double_up(args={"f":32, "d":4096}):
    conv = InputLayer((None,16,128,128))
    conv = GaussianNoiseLayer(conv, args["sigma"])
    for i in range(0, 4):
        for j in range(2):
            conv = Conv2DLayer(conv, num_filters=(i+1)*args["f"], filter_size=3, nonlinearity=args["nonlinearity"])
        #if i != 3:
        conv = MaxPool2DLayer(conv, pool_size=2)
    # coding layer
    if "d" in args:
        conv = DenseLayer(conv, num_units=args["d"], nonlinearity=args["nonlinearity"])
    for layer in get_all_layers(conv)[::-1]:
        if isinstance(layer, InputLayer):
            break
        conv = InverseLayer(conv, layer)
    for layer in get_all_layers(conv):
        print layer, layer.output_shape
    print count_params(layer)
    return conv

def autoencoder_basic_double_up_512(args):
    conv = InputLayer((None,16,128,128))
    conv = GaussianNoiseLayer(conv, args["sigma"])
    for i in range(0, 4):
        for j in range(2):
            conv = Conv2DLayer(conv, num_filters=(i+1)*args["f"], filter_size=3, nonlinearity=args["nonlinearity"])
        if i != 3:
            conv = MaxPool2DLayer(conv, pool_size=2)
    for j in range(2):
        conv = Conv2DLayer(conv, num_filters=512, filter_size=3, nonlinearity=args["nonlinearity"])
    # coding layer
    if "d" in args:
        conv = DenseLayer(conv, num_units=args["d"], nonlinearity=args["nonlinearity"])
    for layer in get_all_layers(conv)[::-1]:
        if isinstance(layer, InputLayer):
            break
        conv = InverseLayer(conv, layer)
    for layer in get_all_layers(conv):
        print layer, layer.output_shape
    print count_params(layer)
    return conv

def autoencoder_basic_double_up_512_stride(args={"f":32, "d":4096}):
    conv = InputLayer((None,16,128,128))
    conv = GaussianNoiseLayer(conv, args["sigma"])
    for i in range(0, 4):
        for j in range(2):
            conv = Conv2DLayer(conv, num_filters=(i+1)*args["f"], filter_size=3, nonlinearity=softplus)
        if i != 3:
            conv = Conv2DLayer(conv, num_filters=(i+1)*args["f"], filter_size=3, stride=2, nonlinearity=softplus)
    for j in range(2):
        conv = Conv2DLayer(conv, num_filters=512, filter_size=3, nonlinearity=softplus)
    # coding layer
    if "d" in args:
        conv = DenseLayer(conv, num_units=args["d"], nonlinearity=softplus)
    for layer in get_all_layers(conv)[::-1]:
        if isinstance(layer, InputLayer):
            break
        conv = InverseLayer(conv, layer)
    for layer in get_all_layers(conv):
        print layer, layer.output_shape
    print count_params(layer)
    return conv

def iterate(X_train, bs=32):
    b = 0
    while True:
        if b*bs >= X_train.shape[0]:
            break
        yield X_train[b*bs:(b+1)*bs]
        b += 1

def train(cfg, num_epochs, out_folder, sched={}, batch_size=128, model_folder="models", tmp_folder="tmp", days=1, data_dir="/storeSSD/cbeckham/nersc/big_images/", debug=True):
    # extract methods
    train_fn, loss_fn, out_fn = cfg["train_fn"], cfg["loss_fn"], cfg["out_fn"]
    lr = cfg["lr"]
    if not os.path.exists(out_folder):
        os.makedirs(out_folder)
    with open("%s/results.txt" % out_folder, "wb") as f:
        f.write("epoch,avg_train_loss,avg_valid_loss,time\n")
        print "epoch,avg_train_loss,avg_valid_loss,time"
        for epoch in range(0, num_epochs):
            t0 = time()
            # learning rate schedule
            if epoch+1 in sched:
                lr.set_value( floatX(sched[epoch+1]) )
                sys.stderr.write("changing learning rate to: %f\n" % sched[epoch+1])
            train_losses = []
            first_minibatch = True
            for X_train, y_train in common.data_iterator(batch_size, data_dir, days=days):
                # shape is (32, 1, 16, 128, 128), so collapse to a 4d tensor
                X_train = X_train.reshape(X_train.shape[0], X_train.shape[2], X_train.shape[3], X_train.shape[4])
                if first_minibatch:
                    X_train_sample = X_train[0:1]
                    first_minibatch = False
                train_losses.append(train_fn(X_train))
            if debug:
                mem = virtual_memory()
                print mem
            # DEBUG: visualise the reconstructions
            img_orig = X_train_sample
            img_reconstruct = out_fn(img_orig)
            img_composite = np.vstack((img_orig[0],img_reconstruct[0]))
            for j in range(0,32):
                plt.subplot(8,4,j+1)
                plt.imshow(img_composite[j])
                plt.axis('off')
            plt.savefig('%s/%i.png' % (out_folder, epoch))
            pyplot.clf()
             
            valid_losses = []
            # todo
            #
            #
            # time
            time_taken = time() - t0
            # print statistics
            print "%i,%f,%f,%f" % (epoch+1, np.mean(train_losses), np.mean(valid_losses), time_taken)
            f.write("%i,%f,%f,%f\n" % (epoch+1, np.mean(train_losses), np.mean(valid_losses), time_taken))
            f.flush()
            # save model at each epoch
            #with open("%s/%i.model" % (out_folder, epoch), "wb") as g:
            #    pickle.dump( get_all_param_values(cfg["l_out"]), g, pickle.HIGHEST_PROTOCOL)


if __name__ == "__main__":
    if "BASIC_TEST_1_DAY" in os.environ:
        # - no noising
        # - somewhat beefy architecture
        # - 1 day only (to keep training fast)
        args = { "learning_rate": 0.01, "sigma":0. }
        net_cfg = get_net(autoencoder_basic, args)
        train(net_cfg, num_epochs=300, batch_size=64, out_file="output/basic_test_1_day.txt", sched={100:0.001,200:0.0001})
    if "BASIC_TEST_1_DAY_RMSPROP" in os.environ:
        # - no work
        args = { "learning_rate": 0.01, "sigma":0., "rmsprop":True, "f":32, "d":4096 }
        net_cfg = get_net(autoencoder_basic_128, args)
        train(net_cfg, num_epochs=300, batch_size=64, out_file="output/basic_test_1_day_rmsprop.txt")
    if "BASIC_TEST_1_DAY_2" in os.environ:
        args = { "learning_rate": 0.01, "sigma":0., "f":64, "d": 4096 }
        net_cfg = get_net(autoencoder_basic_128, args)
        train(net_cfg, num_epochs=300, batch_size=64, out_file="output/basic_test_1_day_beefier.txt", sched={100:0.001,200:0.0001})
    if "BASIC_TEST_1_DAY_3" in os.environ:
        args = { "learning_rate": 0.01, "sigma":0., "f":64 }
        net_cfg = get_net(autoencoder_basic_128, args)
        train(net_cfg, num_epochs=300, batch_size=64, out_folder="output/basic_test_1_day_beefier_no_dense", sched={100:0.001,200:0.0001})
    if "BASIC_TEST_1_DAY_4" in os.environ:
        args = { "learning_rate": 0.01, "sigma":0., "f":64, "d":4096 }
        net_cfg = get_net(autoencoder_basic_128_double_up, args)
        train(net_cfg, num_epochs=300, batch_size=32, out_folder="output/basic_test_1_day_beefier_double_up", sched={100:0.001,200:0.0001})
    if "BASIC_TEST_1_DAY_5" in os.environ:
        args = { "learning_rate": 0.01, "sigma":0., "f":64, "d":4096 }
        net_cfg = get_net(autoencoder_basic_double_up_512, args)
        train(net_cfg, num_epochs=300, batch_size=32, out_folder="output/basic_test_1_day_beefier_double_up_512", sched={100:0.001,200:0.0001})
    if "BASIC_TEST_1_DAY_5_RELU" in os.environ:
        args = { "learning_rate": 0.01, "sigma":0., "f":64, "d":4096, "nonlinearity":rectify }
        net_cfg = get_net(autoencoder_basic_double_up_512, args)
        train(net_cfg, num_epochs=300, batch_size=32, out_folder="output/basic_test_1_day_beefier_double_up_512_relu", sched={100:0.001,200:0.0001})
    if "BASIC_TEST_1_DAY_5_SIGMA1" in os.environ:
        args = { "learning_rate": 0.1, "sigma":1., "f":64, "d":4096 }
        net_cfg = get_net(autoencoder_basic_double_up_512, args)
        train(net_cfg, num_epochs=300, batch_size=32, out_folder="output/basic_test_1_day_beefier_double_up_512_sigma1", sched={100:0.001,200:0.0001})

    # ------------------


    if "BASIC_TEST_1_DAY_RELU_F64" in os.environ:
        args = { "learning_rate": 0.01, "sigma":0., "nonlinearity":rectify, "f":64, "d":4096 }
        net_cfg = get_net(autoencoder_basic, args)
        train(net_cfg, num_epochs=300, batch_size=64, out_folder="output/basic_test_1_day_relu", sched={100:0.001,200:0.0001})


    """
    for epoch in range(10):
        for X_train, y_train in common.data_iterator(128, "/storeSSD/cbeckham/nersc/big_images/", days=1):
            mem = virtual_memory()
            print mem
            pass
    """
    
