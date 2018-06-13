# Authors:
# Christian F. Baumgartner (c.f.baumgartner@gmail.com)
# Lisa M. Koch (lisa.margret.koch@gmail.com)

import logging
import os.path
import time
import shutil
import tensorflow as tf
import numpy as np
import utils
import image_utils
import model as model
from background_generator import BackgroundGenerator
import config.system as sys_config
import acdc_data
import random_walker
import h5py
from scipy.ndimage.filters import gaussian_filter

### EXPERIMENT CONFIG FILE #############################################################
# Set the config file of the experiment you want to run here:

#from experiments import test as exp_config
#from experiments import unet2D_ws_spot_blur as exp_config
from experiments import no_rw_min_exp as exp_config
# from experiments import unet3D_bn_modified as exp_config
# from experiments import unet2D_bn_wxent as exp_config
# from experiments import FCN8_bn_wxent as exp_config

########################################################################################

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')

log_dir = os.path.join(sys_config.log_root, exp_config.experiment_name)

# Set SGE_GPU environment variable if we are not on the local host
#sys_config.setup_GPU_environment()
os.environ["CUDA_VISIBLE_DEVICES"] = "0"


just_started = True

try:
    import cv2
except:
    logging.warning('Could not find cv2. If you want to use augmentation '
                    'function you need to setup OpenCV.')


def run_training(continue_run):

    logging.info('EXPERIMENT NAME: %s' % exp_config.experiment_name)
    already_created_recursion = False
    print("ALready created recursion : " + str(already_created_recursion))
    init_step = 0
    # Load data
    base_data, recursion_data, recursion = acdc_data.load_and_maybe_process_scribbles(scribble_file=sys_config.project_root + exp_config.scribble_data,
                                                                                      target_folder=log_dir,
                                                                                      percent_full_sup=exp_config.percent_full_sup,
                                                                                      scr_ratio=exp_config.length_ratio
                                                                                      )
    #wrap everything from this point onwards in a try-except to catch keyboard interrupt so
    #can control h5py closing data
    try:
        loaded_previous_recursion = False
        start_epoch = 0
        if continue_run:
            logging.info('!!!!!!!!!!!!!!!!!!!!!!!!!!!! Continuing previous run !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!')
            try:
                try:
                    init_checkpoint_path = utils.get_latest_model_checkpoint_path(log_dir, 'recursion_{}_model.ckpt'.format(recursion))
                
                except:
                    print("EXCEPTE GİRDİ")
                    init_checkpoint_path = utils.get_latest_model_checkpoint_path(log_dir, 'recursion_{}_model.ckpt'.format(recursion - 1))
                    loaded_previous_recursion = True
                logging.info('Checkpoint path: %s' % init_checkpoint_path)
                init_step = int(init_checkpoint_path.split('/')[-1].split('-')[-1]) + 1  # plus 1 b/c otherwise starts with eval
                start_epoch = int(init_step/(len(base_data['images_train'])/4))
                logging.info('Latest step was: %d' % init_step)
                logging.info('Continuing with epoch: %d' % start_epoch)
            except:
                logging.warning('!!! Did not find init checkpoint. Maybe first run failed. Disabling continue mode...')
                continue_run = False
                init_step = 0
                start_epoch = 0

            logging.info('!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!')


        if loaded_previous_recursion:
            logging.info("Data file exists for recursion {} "
                         "but checkpoints only present up to recursion {}".format(recursion, recursion - 1))
            logging.info("Likely means postprocessing was terminated")
            
            if not already_created_recursion:
                
                recursion_data = acdc_data.load_different_recursion(recursion_data, -1)
                recursion-=1
            else:
                start_epoch = 0
                init_step = 0
        # load images and validation data
        images_train = np.array(base_data['images_train'])
        scribbles_train = np.array(base_data['scribbles_train'])
        images_val = np.array(base_data['images_test'])
        labels_val = np.array(base_data['masks_test'])

        # if exp_config.use_data_fraction:
        #     num_images = images_train.shape[0]
        #     new_last_index = int(float(num_images)*exp_config.use_data_fraction)
        #
        #     logging.warning('USING ONLY FRACTION OF DATA!')
        #     logging.warning(' - Number of imgs orig: %d, Number of imgs new: %d' % (num_images, new_last_index))
        #     images_train = images_train[0:new_last_index,...]
        #     labels_train = labels_train[0:new_last_index,...]

        logging.info('Data summary:')
        logging.info(' - Images:')
        logging.info(images_train.shape)
        logging.info(images_train.dtype)
        #logging.info(' - Labels:')
        #logging.info(labels_train.shape)
        #logging.info(labels_train.dtype)

        # Tell TensorFlow that the model will be built into the default Graph.
        config = tf.ConfigProto()
        config.gpu_options.allow_growth = True
        config.allow_soft_placement = True
#        with tf.Graph().as_default():
        with tf.Session(config = config) as sess:
            # Generate placeholders for the images and labels.

            image_tensor_shape = [exp_config.batch_size] + list(exp_config.image_size) + [1]
            mask_tensor_shape = [exp_config.batch_size] + list(exp_config.image_size)

            images_placeholder = tf.placeholder(tf.float32, shape=image_tensor_shape, name='images')
            labels_placeholder = tf.placeholder(tf.uint8, shape=mask_tensor_shape, name='labels')

            learning_rate_placeholder = tf.placeholder(tf.float32, shape=[])
            training_time_placeholder = tf.placeholder(tf.bool, shape=[])

            tf.summary.scalar('learning_rate', learning_rate_placeholder)

            # Build a Graph that computes predictions from the inference model.
            logits = model.inference(images_placeholder,
                                     exp_config.model_handle,
                                     training=training_time_placeholder,
                                     nlabels=exp_config.nlabels)

            # Add to the Graph the Ops for loss calculation.
            [loss, _, weights_norm] = model.loss(logits,
                                                 labels_placeholder,
                                                 nlabels=exp_config.nlabels,
                                                 loss_type=exp_config.loss_type,
                                                 weight_decay=exp_config.weight_decay)  # second output is unregularised loss

            tf.summary.scalar('loss', loss)
            tf.summary.scalar('weights_norm_term', weights_norm)

            crf_variables = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='crf_scope')
            print(str(crf_variables))
#            crf_weights = []
#            crf_weights_name = []
#            for v in tf.all_variables():
#                
#                if v.name[0:4]=='bila':
#                    print(str(v))
#                    crf_weights.append(v)
#                    crf_weights_name.append(v.name)
#                elif v.name[0:4] =='spat':
#                    print(str(v))
#                    crf_weights_name.append(v.name)
#                    crf_weights.append(v)
#                elif v.name[0:4] =='comp':
#                    print(str(v))
#                    crf_weights.append(v)
#                    crf_weights_name.append(v.name)
            restore_var = [v for v in tf.all_variables() if v.name not in crf_variables]
            
            # Add to the Graph the Ops that calculate and apply gradients.
   
            global_step = tf.Variable(0, name='global_step', trainable=False)

            network_train_op = tf.train.AdamOptimizer(learning_rate=learning_rate_placeholder).minimize(loss,
                                                                                         var_list=restore_var,
                                                                                         colocate_gradients_with_ops=True,
                                                                                         global_step=global_step)

            crf_train_op = tf.train.AdamOptimizer(learning_rate=1e-07).minimize(loss,var_list=crf_variables,
                                                                                         colocate_gradients_with_ops=True,
                                                                                         global_step=global_step)
            

            # Add the Op to compare the logits to the labels during evaluation.
#            eval_loss = model.evaluation(logits,
#                                         labels_placeholder,
#                                         images_placeholder,
#                                         nlabels=exp_config.nlabels,
#                                         loss_type=exp_config.loss_type,
#                                         weak_supervision=True,
#                                         cnn_threshold=exp_config.cnn_threshold,
#                                         include_bg=True)
            eval_val_loss = model.evaluation(logits,
                                             labels_placeholder,
                                             images_placeholder,
                                             nlabels=exp_config.nlabels,
                                             loss_type=exp_config.loss_type,
                                             weak_supervision=True,
                                             cnn_threshold=exp_config.cnn_threshold,
                                             include_bg=False)

            # Build the summary Tensor based on the TF collection of Summaries.
            summary = tf.summary.merge_all()

            # Add the variable initializer Op.
            init = tf.global_variables_initializer()

            # Create a saver for writing training checkpoints.
            # Only keep two checkpoints, as checkpoints are kept for every recursion
            # and they can be 300MB +
            saver = tf.train.Saver(max_to_keep=2)
            saver_best_dice = tf.train.Saver(max_to_keep=2)
            saver_best_xent = tf.train.Saver(max_to_keep=2)

            # Create a session for running Ops on the Graph.
            sess = tf.Session()

            # Instantiate a SummaryWriter to output summaries and the Graph.
            summary_writer = tf.summary.FileWriter(log_dir, sess.graph)

            # with tf.name_scope('monitoring'):

            val_error_ = tf.placeholder(tf.float32, shape=[], name='val_error')
            val_error_summary = tf.summary.scalar('validation_loss', val_error_)

            val_dice_ = tf.placeholder(tf.float32, shape=[], name='val_dice')
            val_dice_summary = tf.summary.scalar('validation_dice', val_dice_)

            val_summary = tf.summary.merge([val_error_summary, val_dice_summary])

            train_error_ = tf.placeholder(tf.float32, shape=[], name='train_error')
            train_error_summary = tf.summary.scalar('training_loss', train_error_)

            train_dice_ = tf.placeholder(tf.float32, shape=[], name='train_dice')
            train_dice_summary = tf.summary.scalar('training_dice', train_dice_)

            train_summary = tf.summary.merge([train_error_summary, train_dice_summary])

            # Run the Op to initialize the variables.
            sess.run(init)
            
#            crf_training_variables =tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='crf_training_op')
#            print(str(crf_training_variables))
#            all_crf_variables = crf_training_variables+crf_variables
#            print(str(all_crf_variables))
            
            network_variables = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES, scope='network_scope')
            load_variables = []
            for v in network_variables:
                if 'Adam' in v.name:
                    continue
                else:
                    load_variables.append(v)
                # Restore session
#            crf_weights = []
#            for v in tf.all_variables():
#                
#                if v.name[0:4]=='bila':
#                    print(str(v))
#                    crf_weights.append(v.name)
#                elif v.name[0:4] =='spat':
#                    print(str(v))
#                    crf_weights.append(v.name)
#                elif v.name[0:4] =='comp':
#                    print(str(v))
#                    crf_weights.append(v.name)
#            restore_var = [v for v in tf.all_variables() if v.name not in all_crf_variables]


#/////////////////////

#            load_saver = tf.train.Saver(var_list=load_variables)
#            load_saver.restore(sess, '/scratch_net/biwirender02/cany/basil/logdir/unet2D_ws_spot_blur/recursion_0_model.ckpt-7499')

#///////////////////////////

#            if continue_run:
#                # Restore session
#                saver.restore(sess, init_checkpoint_path)
#            saver.restore(sess,'/scratch_net/biwirender02/cany/scribble/logdir/unet2D_ws_spot_blur_crf/recursion_0_model_best_dice.ckpt-14599')
            step = init_step
            curr_lr = exp_config.learning_rate

            no_improvement_counter = 0
            best_val = np.inf
            last_train = np.inf
            loss_history = []
            loss_gradient = np.inf
            best_dice = 0
            logging.info('RECURSION {0}'.format(recursion))

            


            # random walk - if it already has been random walked it won't redo
            
            recursion_data = acdc_data.random_walk_epoch(recursion_data, exp_config.rw_beta, exp_config.rw_threshold, exp_config.random_walk)
            print("Random walku geçti")
            #get ground truths
            labels_train = np.array(recursion_data['random_walked'])
            print("Start epoch : " +str(start_epoch) + " : max epochs : " + str(exp_config.epochs_per_recursion))
            for epoch in range(start_epoch, exp_config.max_epochs):
                if (epoch % exp_config.epochs_per_recursion == 0 and epoch != 0) \
                        or loaded_previous_recursion:

                    
                    loaded_previous_recursion = False
                    #Have reached end of recursion
                    recursion_data = predict_next_gt(data=recursion_data,
                                                     images_train=images_train,
                                                     images_placeholder=images_placeholder,
                                                     training_time_placeholder=training_time_placeholder,
                                                     logits=logits,
                                                     sess=sess)

                    recursion += 1

                    #get ground truths
                    labels_train = np.array(recursion_data['predicted'])

                    #reinitialise savers - otherwise, no checkpoints will be saved for each recursion
                    saver = tf.train.Saver(max_to_keep=2)
                    saver_best_dice = tf.train.Saver(max_to_keep=2)
                    saver_best_xent = tf.train.Saver(max_to_keep=2)
                logging.info('Epoch {0} ({1} of {2} epochs for recursion {3})'.format(epoch,
                                                                                      1 + epoch % exp_config.epochs_per_recursion,
                                                                                      exp_config.epochs_per_recursion,
                                                                                      recursion))
                # for batch in iterate_minibatches(images_train,
                #                                  labels_train,
                #                                  batch_size=exp_config.batch_size,
                #                                  augment_batch=exp_config.augment_batch):

                # You can run this loop with the BACKGROUND GENERATOR, which will lead to some improvements in the
                # training speed. However, be aware that currently an exception inside this loop may not be caught.
                # The batch generator may just continue running silently without warning even though the code has
                # crashed.

                for batch in BackgroundGenerator(iterate_minibatches(images_train,
                                                                     labels_train,
                                                                     batch_size=exp_config.batch_size,
                                                                     augment_batch=exp_config.augment_batch)):

                    if exp_config.warmup_training:
                        if step < 50:
                            curr_lr = exp_config.learning_rate / 10.0
                        elif step == 50:
                            curr_lr = exp_config.learning_rate

                    start_time = time.time()

                    # batch = bgn_train.retrieve()
                    x, y = batch

                    # TEMPORARY HACK (to avoid incomplete batches
                    if y.shape[0] < exp_config.batch_size:
                        step += 1
                        continue

                    feed_dict = {
                        images_placeholder: x,
                        labels_placeholder: y,
                        learning_rate_placeholder: curr_lr,
                        training_time_placeholder: True
                    }
                    
                    if (step % 1000 == 0):
                        print("CRF variables : " + str(sess.run(crf_variables)))
                    if (step % 10 == 0) :
                        _, loss_value = sess.run([crf_train_op, loss], feed_dict=feed_dict)
                    _, loss_value = sess.run([network_train_op, loss], feed_dict=feed_dict)
                    duration = time.time() - start_time

                    # Write the summaries and print an overview fairly often.
                    if step % 10 == 0:
                        # Print status to stdout.
                        logging.info('Step %d: loss = %.6f (%.3f sec)' % (step, loss_value, duration))
                        # Update the events file.

                        summary_str = sess.run(summary, feed_dict=feed_dict)
                        summary_writer.add_summary(summary_str, step)
                        summary_writer.flush()


                    # Save a checkpoint and evaluate the model periodically.
                    if (step + 1) % exp_config.val_eval_frequency == 0:

                        checkpoint_file = os.path.join(log_dir, 'recursion_{}_model.ckpt'.format(recursion))
                        saver.save(sess, checkpoint_file, global_step=step)
                        # Evaluate against the training set.


                        # Evaluate against the validation set.
                        logging.info('Validation Data Eval:')
                        [val_loss, val_dice] = do_eval(sess,
                                                       eval_val_loss,
                                                       images_placeholder,
                                                       labels_placeholder,
                                                       training_time_placeholder,
                                                       images_val,
                                                       labels_val,
                                                       exp_config.batch_size)

                        val_summary_msg = sess.run(val_summary, feed_dict={val_error_: val_loss, val_dice_: val_dice}
                        )
                        summary_writer.add_summary(val_summary_msg, step)

                        if val_dice > best_dice:
                            best_dice = val_dice
                            best_file = os.path.join(log_dir, 'recursion_{}_model_best_dice.ckpt'.format(recursion))
                            saver_best_dice.save(sess, best_file, global_step=step)
                            logging.info('Found new best dice on validation set! - {} - '
                                         'Saving recursion_{}_model_best_dice.ckpt' .format(val_dice, recursion))
                            text_file = open('val_results.txt', "a")
                            text_file.write("\nVal dice " + str(step) +" : " + str(val_dice))
                            text_file.close()
                        if val_loss < best_val:
                            best_val = val_loss
                            best_file = os.path.join(log_dir, 'recursion_{}_model_best_xent.ckpt'.format(recursion))
                            saver_best_xent.save(sess, best_file, global_step=step)
                            logging.info('Found new best crossentropy on validation set! - {} - '
                                         'Saving recursion_{}_model_best_xent.ckpt'.format(val_loss, recursion))


                    step += 1

    except Exception:
        raise
    # except (KeyboardInterrupt, SystemExit):
    #     try:
    #         recursion_data.close();
    #         logging.info('Keyboard interrupt / system exit caught - successfully closed data file.')
    #     except:
    #         logging.info('Keyboard interrupt / system exit caught - could not close data file.')




def predict_next_gt(data,
                    images_train,
                    images_placeholder,
                    training_time_placeholder,
                    logits,
                    sess):
    '''
    Uses current network weights to segment images for next recursion
    After postprocessing, these are used as the ground truth for further training
    :param data: Data of the current recursion - if this is of recursion n, this function
                 predicts ground truths for recursion n + 1
    :param images_train: Numpy array of training images
    :param images_placeholder: Tensorflow placeholder for image feed
    :param training_time_placeholder: Boolean tensorflow placeholder
    :param logits: Logits operator for calculating segmentation mask probabilites
    :param sess: Tensorflow session
    :return: The data file for recursion n + 1
    '''
    #get recursion from filename
    recursion = utils.get_recursion_from_hdf5(data)

    new_recursion_fname = acdc_data.recursion_filepath(recursion + 1, data_file=data)
    if not os.path.exists(new_recursion_fname):
        fpath = os.path.dirname(data.filename)
        data.close()
        data = acdc_data.create_recursion_dataset(fpath, recursion + 1)
    else:
        data.close()
        data = h5py.File(new_recursion_fname, 'r+')

    #attributes to track processing
    prediction = data['predicted']
    processed = data['predicted'].attrs.get('processed')
    if not processed:
        processed_to = data['predicted'].attrs.get('processed_to')
        scr_max = len(images_train)
        print("SCR max = " + str(scr_max))
        for scr_idx in range(processed_to, scr_max, exp_config.batch_size):
            if scr_idx+exp_config.batch_size > scr_max:
                print("Entered last")
                # At the end of the dataset
                # Must ensure feed_dict is 20 images long however
                ind = list(range(scr_max - exp_config.batch_size, scr_max))
            else:
                
                ind = list(range(scr_idx, scr_idx + exp_config.batch_size))
                print(str(ind))
#            logging.info('Saving prediction after recursion {0} for images {1} to {2} '
#                         .format(ind[0], ind[-1]))
            x = np.expand_dims(np.array(images_train[ind, ...]), -1)

            feed_dict = {
                images_placeholder: x,
                training_time_placeholder: False
            }
            softmax = tf.nn.softmax(logits)

            print("softmax")
            #threshold output of cnn
            if exp_config.cnn_threshold:
                threshold = tf.constant(exp_config.cnn_threshold, dtype=tf.float32)
                s = tf.multiply(tf.ones(shape=[exp_config.batch_size, 212, 212, 1]), threshold)
                softmax = tf.concat([s, softmax[..., 1:]], axis=-1)
            print("threshold")
            # if exp_config.use_crf:
            #     #get unary from softmax
            #     unary = tf.multiply(-1, tf.log(softmax))
            #
            #calculate mask
            mask = tf.arg_max(softmax, dimension=-1)
            
            print("before sess")
            mask_out = sess.run(mask, feed_dict=feed_dict)
            print("after sess : " + str(mask_out))
            #save to dataset
            for indice in range(len(ind)):
                
                prediction[ind[indice], ...] = np.squeeze(mask_out[indice, ...])
                print("added " + str(ind[indice]))
            data['predicted'].attrs.modify('processed_to', scr_idx + exp_config.batch_size)

        if exp_config.reinit:
            logging.info("Initialising variables")
            sess.run(tf.global_variables_initializer())

        data['predicted'].attrs.modify('processed', True)
        logging.info('Created unprocessed ground truths for recursion {}'.format(recursion + 1))

        #Reopen in read only mode
        data.close()
        data = h5py.File(new_recursion_fname, 'r')
    return data





def do_eval(sess,
            eval_loss,
            images_placeholder,
            labels_placeholder,
            training_time_placeholder,
            images,
            labels,
            batch_size):

    '''
    Function for running the evaluations every X iterations on the training and validation sets. 
    :param sess: The current tf session 
    :param eval_loss: The placeholder containing the eval loss
    :param images_placeholder: Placeholder for the images
    :param labels_placeholder: Placeholder for the masks
    :param training_time_placeholder: Placeholder toggling the training/testing mode. 
    :param images: A numpy array or h5py dataset containing the images
    :param labels: A numpy array or h45py dataset containing the corresponding labels 
    :param batch_size: The batch_size to use. 
    :return: The average loss (as defined in the experiment), and the average dice over all `images`. 
    '''

    loss_ii = 0
    dice_ii = 0
    num_batches = 0

    for batch in iterate_minibatches(images, labels, batch_size=batch_size, augment_batch=False):  # No aug in evaluation
    # As before you can wrap the iterate_minibatches function in the BackgroundGenerator class for speed improvements
    # but at the risk of not catching exceptions

        x, y = batch

        if y.shape[0] < batch_size:
            continue

        feed_dict = { images_placeholder: x,
                      labels_placeholder: y,
                      training_time_placeholder: False}

        closs, cdice = sess.run(eval_loss, feed_dict=feed_dict)
        loss_ii += closs
        dice_ii += cdice
        num_batches += 1

    avg_loss = loss_ii / num_batches
    avg_dice = dice_ii / num_batches

    logging.info('  Average loss: %0.04f, average dice: %0.04f' % (avg_loss, avg_dice))

    return avg_loss, avg_dice


def augmentation_function(images, labels, **kwargs):
    '''
    Function for augmentation of minibatches. It will transform a set of images and corresponding labels
    by a number of optional transformations. Each image/mask pair in the minibatch will be seperately transformed
    with random parameters. 
    :param images: A numpy array of shape [minibatch, X, Y, (Z), nchannels]
    :param labels: A numpy array containing a corresponding label mask
    :param do_rotations: Rotate the input images by a random angle between -15 and 15 degrees.
    :param do_scaleaug: Do scale augmentation by sampling one length of a square, then cropping and upsampling the image
                        back to the original size. 
    :param do_fliplr: Perform random flips with a 50% chance in the left right direction. 
    :return: A mini batch of the same size but with transformed images and masks. 
    '''

    if images.ndim > 4:
        raise AssertionError('Augmentation will only work with 2D images')

    do_rotations = kwargs.get('do_rotations', False)
    do_scaleaug = kwargs.get('do_scaleaug', False)
    do_fliplr = kwargs.get('do_fliplr', False)


    new_images = []
    new_labels = []
    num_images = images.shape[0]

    for ii in range(num_images):

        img = np.squeeze(images[ii,...])
        lbl = np.squeeze(labels[ii,...])

        # ROTATE
        if do_rotations:
            angles = kwargs.get('angles', (-15,15))
            random_angle = np.random.uniform(angles[0], angles[1])
            img = image_utils.rotate_image(img, random_angle)
            lbl = image_utils.rotate_image(lbl, random_angle, interp=cv2.INTER_NEAREST)

        # RANDOM CROP SCALE
        if do_scaleaug:
            offset = kwargs.get('offset', 30)
            n_x, n_y = img.shape
            r_y = np.random.random_integers(n_y-offset, n_y)
            p_x = np.random.random_integers(0, n_x-r_y)
            p_y = np.random.random_integers(0, n_y-r_y)

            img = image_utils.resize_image(img[p_y:(p_y+r_y), p_x:(p_x+r_y)],(n_x, n_y))
            lbl = image_utils.resize_image(lbl[p_y:(p_y + r_y), p_x:(p_x + r_y)], (n_x, n_y), interp=cv2.INTER_NEAREST)

        # RANDOM FLIP
        if do_fliplr:
            coin_flip = np.random.randint(2)
            if coin_flip == 0:
                img = np.fliplr(img)
                lbl = np.fliplr(lbl)


        new_images.append(img[..., np.newaxis])
        new_labels.append(lbl[...])

    sampled_image_batch = np.asarray(new_images)
    sampled_label_batch = np.asarray(new_labels)

    return sampled_image_batch, sampled_label_batch


def iterate_minibatches(images, labels, batch_size, augment_batch=False):
    '''
    Function to create mini batches from the dataset of a certain batch size 
    :param images: hdf5 dataset
    :param labels: hdf5 dataset
    :param batch_size: batch size
    :param augment_batch: should batch be augmented?
    :return: mini batches
    '''

    random_indices = np.arange(images.shape[0])
    np.random.shuffle(random_indices)

    n_images = images.shape[0]

    for b_i in range(0, n_images, batch_size):

        if b_i + batch_size > n_images:
            continue

        # HDF5 requires indices to be in increasing order
        batch_indices = np.sort(random_indices[b_i:b_i+batch_size])

        X = images[batch_indices, ...]
        y = labels[batch_indices, ...]

        image_tensor_shape = [X.shape[0]] + list(exp_config.image_size) + [1]
        X = np.reshape(X, image_tensor_shape)

        if augment_batch:
            X, y = augmentation_function(X, y,
                                         do_rotations=exp_config.do_rotations,
                                         do_scaleaug=exp_config.do_scaleaug,
                                         do_fliplr=exp_config.do_fliplr)

        yield X, y


def main():

    continue_run = True
    if not tf.gfile.Exists(log_dir):
        tf.gfile.MakeDirs(log_dir)
        continue_run = False

    # Copy experiment config file
    shutil.copy(exp_config.__file__, log_dir)

    run_training(continue_run)


if __name__ == '__main__':

    # parser = argparse.ArgumentParser(
    #     description="Train a neural network.")
    # parser.add_argument("CONFIG_PATH", type=str, help="Path to config file (assuming you are in the working directory)")
    # args = parser.parse_args()

    main()