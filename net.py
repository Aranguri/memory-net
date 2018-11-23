import tensorflow as tf
import itertools
from utils import *
from lstm_layer import LSTM
from tensorflow.python import debug as tf_debug

learning_rate = 1e-2
beta1 = 0.9
beta2 = 0.999

batch_size = 32
memory_length = 32
memory_size = 8
bits_length = 8 #copy task: length of the array the nn has to remember
input_length = output_length = bits_length * 2
output_size = input_size = 4
h_size = 30

#next step (@santi): we need to continue debugging. the actual thing is that the memory gets nan values. oh, I
# could read the paper to understand why the NaN appeared. I could build a LSTM to test the performance
# however, probably there are some bugs, because I didn't debug enough. besides trying to remove errors, it could be good
# to just look at the code and see if it makes sense. I could also read the paper where they implement the NTM

x = tf.placeholder(tf.float32, shape=(batch_size, input_length, input_size))
y = tf.placeholder(tf.float32, shape=(batch_size, output_length, output_size))
initial_memories = tf.Variable(tf.constant(1e-6, shape=(1, batch_size, memory_length, memory_size)))
initial_memories = tf.stop_gradient(initial_memories) #A1evoid backprop on initial memory

interface_size = memory_size + memory_length + 3
ctrl_out_size = 2 * interface_size + 2 * memory_size + output_size
controller = LSTM(tf.zeros([2, batch_size, h_size]), ctrl_out_size)

def io_head(interface, memory, w_prev):
    # prepare input
    key, shift, gate, b, sharpener = tf.split(interface, [memory_size, memory_length, 1, 1, 1], axis=1)
    shift = tf.nn.softmax(shift)
    gate = tf.sigmoid(gate)
    b = tf.nn.softplus(b) #is there a better choice?
    sharpener = tf.nn.softplus(sharpener) + 1
    shift = tf.reshape(shift, (batch_size, memory_length))
    memory = tf.reshape(memory, (memory_length, batch_size, memory_size))

    #shift, gate, b, sharpener

    def similarity(vec):
        return tf.reduce_sum(vec * key, axis=1) / (tf.norm(vec) * tf.norm(key))

    def convolve(i):
        shift_matrix = tf.manip.roll(tf.reverse(shift, axis=(1,)), shift=((i+1) % memory_length), axis=1)#this shift is prone to bugs :D
        return tf.reduce_sum(w_g * shift_matrix, axis=1)

    #content based weight mixed with previous weight
    unnorm_w_c = tf.map_fn(similarity, memory)
    w_c = unnorm_w_c / tf.reduce_sum(unnorm_w_c, axis=0)
    w_g = gate * tf.transpose(w_c) + (1 - gate) * w_prev
    print_op = tf.Print([memory], [w_g[0]], summarize=10000)

    # convolve weights (this allows the neural net to go forward and backward in memory)
    w_tilde = tf.map_fn(convolve, tf.range(memory_length), dtype=tf.float32)
    w_tilde_num = tf.transpose(w_tilde) ** sharpener
    w_tilde_den = tf.reduce_sum(w_tilde_num, axis=1, keepdims=True)
    w = w_tilde_num / w_tilde_den

    with tf.control_dependencies([print_op]):
        w = tf.reshape(w, (batch_size, memory_length, 1))
    return w

def cond(output, memories, r, controller_hs, w_prevs):
    return tf.reduce_any(tf.not_equal(tf.shape(output), tf.shape(y)))

def body(output, memories, r, controller_hs, w_prevs):
    #prepare input
    memory = memories[-1]
    controller.h = controller_hs
    w_prev_read, w_prev_write = tf.split(w_prevs, [1, 1], axis=0)
    w_prev_read = tf.reshape(w_prev_read, (batch_size, memory_length))
    w_prev_write = tf.reshape(w_prev_write, (batch_size, memory_length))
    x_now = x[:, tf.shape(output)[1]]

    #execute controller
    interface = tf.split(controller(x_now), [interface_size, interface_size, memory_size, memory_size, output_size], axis=1)
    interface_read, interface_write, erase, write, extra_output = interface
    erase = tf.reshape(erase, (batch_size, 1, memory_size))
    write = tf.reshape(write, (batch_size, 1, memory_size))
    extra_output = tf.reshape(extra_output, (batch_size, 1, output_size))
    new_output = tf.concat((output, extra_output), axis=1)

    #read head
    w_read = io_head(interface_read, memory, w_prev_read)
    r = tf.reduce_sum(w_read * memory, axis=1)

    #write head
    w_write = io_head(interface_write, memory, w_prev_write)
    print_op = tf.Print([memory], [1])#w_write[0][0], memory[0][0][0], w_prev_write[0][0]])
    memory = memory * (1 - w_write * erase) + w_write * write
    memory = tf.reshape(memory, (1, batch_size, memory_length, memory_size))
    memories = tf.concat((memories, memory), axis=0)

    # with tf.control_dependencies([print_op]):
    w_prevs = tf.reshape(tf.concat((w_read, w_write), axis=0), (2, batch_size, memory_length))
    return new_output, memories, r, controller.h, w_prevs

#is there a better way of doing this?
shapes = [tf.TensorShape([batch_size, None, output_size]), tf.TensorShape([None, batch_size, memory_length, memory_size]),
          tf.TensorShape([batch_size, memory_size]), tf.TensorShape([2, batch_size, h_size]), tf.TensorShape([2, batch_size, memory_length])]
last_state = tf.while_loop(cond, body, [tf.constant(0.0, shape=(batch_size, 0, output_size)), initial_memories, tf.constant(0.0, shape=(batch_size, memory_size)), controller.h, tf.constant(0.0, shape=(2, batch_size, memory_length))], shape_invariants=[*shapes])
output, memories = last_state[0:2]

optimizer = tf.train.AdamOptimizer(learning_rate, beta1, beta2)
loss = tf.losses.mean_squared_error(output, y)
binary_output = tf.to_float(output > .5)
errors = tf.equal(binary_output, y)
accuracy = tf.reduce_mean(tf.to_float(errors))
minimize = optimizer.minimize(loss)

tf.summary.scalar('loss', loss)
tr_loss, tr_acc = {}, {}

with tf.Session() as sess:
    sess = tf_debug.LocalCLIDebugWrapperSession(sess)
    sess.add_tensor_filter("has_inf_or_nan", tf_debug.has_inf_or_nan)
    sess.run(tf.global_variables_initializer())
    train_writer = tf.summary.FileWriter('./logs/9/train ', sess.graph)

    for i in itertools.count():
        bits = np.random.randint(0, 2, size=(batch_size, bits_length, input_size))#we could vary input_size
        xs = np.concatenate((bits, np.zeros_like(bits)), axis=1)
        ys = np.concatenate((np.zeros_like(bits), bits), axis=1)
        tr_loss[i], _, output_, memories_, tr_acc[i] = sess.run([loss, minimize, errors, memories, accuracy], feed_dict={x: xs, y: ys})
        print(i)
        # print('Loss', tr_loss[i])
            #print(f'{i} loss {np.mean([tr_loss[len(tr_loss) - j] for j in range(1, 100)])}')
        # print(memories_[-1][0][0])
        # if i % 190 == 1:
            # pass
            # print(memories_[-1][0][0])
            #print(output_[0, 8:])
            #print(ys[0, 8:])
            # print()
            #dump to tensorboard
            # merge = tf.summary.merge_all()
            # summary = sess.run([merge], feed_dict={x: xs, y: ys})
            # train_writer.add_summary(summary[0], i)
            # plot(tr_acc)

'''
#NTM things
shift s could be smaller

#things
it's weird that it always arrived to selecting the same memory slot with same attention... maybe something persists over time
So the memory storage is very similar for two different runnings with different inputs.
also, what's the size of selected_memory. shouldn't I be merging all memories into one vectorcñio?
think about the gate. is there an advantage of having dimensionality memory_size?
what could be failing? refer to the drawing

# Next steps
* enable memories larger than only one real number (ie, increment input_size, output_size, memory_size)
* other task: send n real numbers, then send noise, and then ask for the n inputs.
* is it good to let the neural net use distributed representation for memories? or is it better to apply a sharp softargmax that forces the nn to modify only so many memory slots at a time.
* how does the performance difference between rnn w memory and rnn wo memory change as we vary the input_length?
* Do we want to backpropagate through the memory? Is it better to have a fixed initial memory or the optimal one selected by the nn?
* rnn_im could also receive the memory as input
* add multiple read/write in one iteration if nn wishes to do so
* why aren't relu units used for lstm nets?

# Debugging
print('')
plt.ion()
plt.cla()
plt.imshow(memories_[:-1, 0, :])
plt.pause(1e-8)

# Story

# Others
print_op = tf.Print([memory, memory_gate], [memory[0], memory_gate[0]])
with tf.control_dependencies([print_op]):
    new_output = tf.identity(new_output)

# benchmarks
(@2500)
With memory
.13

Without memory
.17
.08
.08

> 0.05

#quotes
'espero no estar haciendo todo mal' (tampoco en la vida)
'tensorflow: el arte de hacer que las shapes coincidan'
'''
