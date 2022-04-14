from keras import backend as K
from keras.engine.topology import Layer
import keras
import tensorflow as tf
import numpy as np
import math
from keras.layers import Dense


class SeqDistanceSelfAttention(Layer):

    ATTENTION_TYPE_ADD = 'additive'
    ATTENTION_TYPE_MUL = 'multiplicative'

    def __init__(self,
                 units=32,
                 attention_width=None,
                 attention_type=ATTENTION_TYPE_ADD,
                 return_attention=False,
                 history_only=False,
                 kernel_initializer='glorot_normal',
                 bias_initializer='zeros',
                 kernel_regularizer=None,
                 bias_regularizer=None,
                 kernel_constraint=None,
                 bias_constraint=None,
                 use_additive_bias=True,
                 use_attention_bias=True,
                 attention_activation=None,
                 attention_distance="linear",
                 attention_regularizer_weight=0.0,
                 **kwargs):
        """Layer initialization.
        For additive attention, see: https://arxiv.org/pdf/1806.01264.pdf
        :param units: The dimension of the vectors that used to calculate the attention weights.
        :param attention_width: The width of local attention.
        :param attention_type: 'additive' or 'multiplicative'.
        :param return_attention: Whether to return the attention weights for visualization.
        :param history_only: Only use historical pieces of data.
        :param kernel_initializer: The initializer for weight matrices.
        :param bias_initializer: The initializer for biases.
        :param kernel_regularizer: The regularization for weight matrices.
        :param bias_regularizer: The regularization for biases.
        :param kernel_constraint: The constraint for weight matrices.
        :param bias_constraint: The constraint for biases.
        :param use_additive_bias: Whether to use bias while calculating the relevance of inputs features
                                  in additive mode.
        :param use_attention_bias: Whether to use bias while calculating the weights of attention.
        :param attention_activation: The activation used for calculating the weights of attention.
        :param attention_regularizer_weight: The weights of attention regularizer.
        :param kwargs: Parameters for parent class.
        """
        super(SeqDistanceSelfAttention, self).__init__(**kwargs)
        self.supports_masking = True
        self.units = units
        self.attention_width = attention_width
        self.attention_type = attention_type
        self.return_attention = return_attention
        self.history_only = history_only
        if history_only and attention_width is None:
            self.attention_width = int(1e9)
        self.use_additive_bias = use_additive_bias
        self.use_attention_bias = use_attention_bias
        self.attention_distance = attention_distance
        self.kernel_initializer = keras.initializers.get(kernel_initializer)
        self.bias_initializer = keras.initializers.get(bias_initializer)
        self.kernel_regularizer = keras.regularizers.get(kernel_regularizer)
        self.bias_regularizer = keras.regularizers.get(bias_regularizer)
        self.kernel_constraint = keras.constraints.get(kernel_constraint)
        self.bias_constraint = keras.constraints.get(bias_constraint)
        self.attention_activation = keras.activations.get(attention_activation)
        self.attention_regularizer_weight = attention_regularizer_weight
        self._backend = keras.backend.backend()

        if attention_type == SeqDistanceSelfAttention.ATTENTION_TYPE_ADD:
            self.Wx, self.Wt, self.bh = None, None, None
            self.Wa, self.ba = None, None
        elif attention_type == SeqDistanceSelfAttention.ATTENTION_TYPE_MUL:
            self.Wa, self.ba = None, None
        else:
            raise NotImplementedError('No implementation for attention type : ' + attention_type)

    def get_config(self):
        config = {
            'units': self.units,
            'attention_width': self.attention_width,
            'attention_type': self.attention_type,
            'return_attention': self.return_attention,
            'history_only': self.history_only,
            'use_additive_bias': self.use_additive_bias,
            'use_attention_bias': self.use_attention_bias,
            'kernel_initializer': keras.initializers.serialize(self.kernel_initializer),
            'bias_initializer': keras.initializers.serialize(self.bias_initializer),
            'kernel_regularizer': keras.regularizers.serialize(self.kernel_regularizer),
            'bias_regularizer': keras.regularizers.serialize(self.bias_regularizer),
            'kernel_constraint': keras.constraints.serialize(self.kernel_constraint),
            'bias_constraint': keras.constraints.serialize(self.bias_constraint),
            'attention_activation': keras.activations.serialize(self.attention_activation),
            'attention_regularizer_weight': self.attention_regularizer_weight,
        }
        base_config = super(SeqDistanceSelfAttention, self).get_config()
        return dict(list(base_config.items()) + list(config.items()))

    def build(self, input_shape):
        if self.attention_type == SeqDistanceSelfAttention.ATTENTION_TYPE_ADD:
            self._build_additive_attention(input_shape)
        elif self.attention_type == SeqDistanceSelfAttention.ATTENTION_TYPE_MUL:
            self._build_multiplicative_attention(input_shape)
        super(SeqDistanceSelfAttention, self).build(input_shape)

    def _build_additive_attention(self, input_shape):
        feature_dim = int(input_shape[2])

        self.Wt = self.add_weight(shape=(feature_dim, self.units),
                                  name='{}_Add_Wt'.format(self.name),
                                  initializer=self.kernel_initializer,
                                  regularizer=self.kernel_regularizer,
                                  constraint=self.kernel_constraint)
        self.Wx = self.add_weight(shape=(feature_dim, self.units),
                                  name='{}_Add_Wx'.format(self.name),
                                  initializer=self.kernel_initializer,
                                  regularizer=self.kernel_regularizer,
                                  constraint=self.kernel_constraint)
        if self.use_additive_bias:
            self.bh = self.add_weight(shape=(self.units,),
                                      name='{}_Add_bh'.format(self.name),
                                      initializer=self.bias_initializer,
                                      regularizer=self.bias_regularizer,
                                      constraint=self.bias_constraint)

        self.Wa = self.add_weight(shape=(self.units, 1),
                                  name='{}_Add_Wa'.format(self.name),
                                  initializer=self.kernel_initializer,
                                  regularizer=self.kernel_regularizer,
                                  constraint=self.kernel_constraint)
        # self.gate_ba = self.add_weight(shape=(1,),
        #                           name='{}_Add_gate_ba'.format(self.name),
        #                           initializer=self.bias_initializer,
        #                           regularizer=self.bias_regularizer,
        #                           constraint=self.bias_constraint)
        if self.use_attention_bias:
            self.ba = self.add_weight(shape=(1,),
                                      name='{}_Add_ba'.format(self.name),
                                      initializer=self.bias_initializer,
                                      regularizer=self.bias_regularizer,
                                      constraint=self.bias_constraint)

    def _build_multiplicative_attention(self, input_shape):
        feature_dim = int(input_shape[2])

        self.Wa = self.add_weight(shape=(feature_dim, feature_dim),
                                  name='{}_Mul_Wa'.format(self.name),
                                  initializer=self.kernel_initializer,
                                  regularizer=self.kernel_regularizer,
                                  constraint=self.kernel_constraint)
        if self.use_attention_bias:
            self.ba = self.add_weight(shape=(1,),
                                      name='{}_Mul_ba'.format(self.name),
                                      initializer=self.bias_initializer,
                                      regularizer=self.bias_regularizer,
                                      constraint=self.bias_constraint)

    def call(self, inputs, mask=None, **kwargs):
        input_len = K.shape(inputs)[1]
        #------生成mask
        bs, sl, vec = K.shape(inputs)[0], K.shape(inputs)[1], K.shape(inputs)[2]
        sl = 15
        # make distance mask
        sl_indices = np.arange(sl)
        sl_col, sl_row = np.meshgrid(sl_indices, sl_indices)
        sub = abs(sl_col - sl_row)

        # distance mask
        if self.attention_distance == 'linear':
            distance_mask = np.zeros([sl, sl])
            for i in range(sl):
                for j in range(sl):
                    if i == j:
                        distance_mask[i][j] = -10000
                    else:
                        distance_mask[i][j] = -abs(sl_col[i][j] - sl_row[i][j])
            distance_mask = tf.convert_to_tensor(distance_mask)
        elif self.attention_distance == 'log':
            distance_mask = np.zeros([sl, sl])
            for i in range(sl):
                for j in range(sl):
                    if i == j:
                        distance_mask[i][j] = -10000
                    else:
                        distance_mask[i][j] = -math.log(abs(sl_col[i][j] - sl_row[i][j]))
        elif self.attention_distance == 'exp':
            distance_mask = np.zeros([sl, sl])
            for i in range(sl):
                for j in range(sl):
                    if i == j:
                        distance_mask[i][j] = -10000
                    else:
                        distance_mask[i][j] = -math.exp(abs(sl_col[i][j] - sl_row[i][j]))
        elif self.attention_distance == 'mydistance':
            distance_mask = np.zeros([sl, sl])
            for i in range(sl):
                for j in range(sl):
                    if i == j:
                        distance_mask[i][j] = -10000
                    elif abs(i - j) > 1:
                        #distance_mask[i][j] = -math.exp(abs(sl_col[i][j] - sl_row[i][j]))
                        distance_mask[i][j] = -abs(sl_col[i][j] - sl_row[i][j])
                        #distance_mask[i][j] = -math.log(abs(sl_col[i][j] - sl_row[i][j]))
                        # distance_mask[i][j] = -1. / (1 + math.exp(abs(sl_col[i][j] - sl_row[i][j])))
        elif self.attention_distance == 'sigmoid':
            distance_mask = np.zeros([sl, sl])
            for i in range(sl):
                for j in range(sl):
                    if i == j:
                        distance_mask[i][j] = -10000
                    elif abs(i - j) > 1:
                        distance_mask[i][j] = -1./(1+math.exp(abs(sl_col[i][j] - sl_row[i][j])))
        elif self.attention_distance == 'tanh':
            distance_mask = np.zeros([sl, sl])
            for i in range(sl):
                for j in range(sl):
                    if i == j:
                        distance_mask[i][j] = -10000
                    elif abs(i - j) > 1:
                        distance_mask[i][j] = -math.tanh(abs(sl_col[i][j] - sl_row[i][j]))

        distance_mask = tf.convert_to_tensor(distance_mask)
        distance_mask = tf.tile(K.expand_dims(distance_mask, 0), [bs, 1, 1])  # bs,sl,sl 对当前张量内的数据进行一定规则的复制

        if mask is not None:
            temp_mask = K.expand_dims(mask, 1)
            mask = tf.tile(temp_mask, [1, sl, 1])  # bs,sl,sl
        if self.attention_type == SeqDistanceSelfAttention.ATTENTION_TYPE_ADD:
            e = self._call_additive_emission(inputs)
        elif self.attention_type == SeqDistanceSelfAttention.ATTENTION_TYPE_MUL:
            e = self._call_multiplicative_emission(inputs)

        if self.attention_activation is not None:
            e = self.attention_activation(e)
        if self.attention_width is not None:
            if self.history_only:
                lower = K.arange(0, input_len) - (self.attention_width - 1)
            else:
                lower = K.arange(0, input_len) - self.attention_width // 2
            lower = K.expand_dims(lower, axis=-1)
            upper = lower + self.attention_width
            indices = K.expand_dims(K.arange(0, input_len), axis=0)
            e -= 10000.0 * (1.0 - K.cast(lower <= indices, K.floatx()) * K.cast(indices < upper, K.floatx()))
        if mask is not None:
            mask = K.cast(mask, K.floatx())
            e-=10000.0*(1.0 - mask)

        distance_mask = K.cast(distance_mask, K.floatx())
        temp_e = e
        e +=distance_mask

        # a_{t} = \text{softmax}(e_t)
        e = K.exp(e - K.max(e, axis=-1, keepdims=True))
        a = e / K.sum(e, axis=-1, keepdims=True)

        # l_t = \sum_{t'} a_{t, t'} x_{t'}
        v = K.batch_dot(a, inputs)
        #v = K.sum(v, axis=1)
        if self.attention_regularizer_weight > 0.0:
            self.add_loss(self._attention_regularizer(a))
        if self.return_attention:
            return [v, a]

        # return [v,mask,temp_e]
        return v

    # def fusion_gate(self,inputs,maskInput):
    #     maskInput = Dense(128, activation='relu')(maskInput)
    #     fusion_gate = K.sigmoid(inputs+maskInput+self.gate_ba)
    #     output = fusion_gate * inputs + (1 - fusion_gate) * maskInput
    #     return output

    def _call_additive_emission(self, inputs):
        scale=5
        input_shape = K.shape(inputs)
        batch_size, input_len = input_shape[0], input_shape[1]

        # h_{t, t'} = \tanh(x_t^T W_t + x_{t'}^T W_x + b_h)
        q = K.expand_dims(K.dot(inputs, self.Wt), 2) #在下标为dim的轴上增加一维 shape=(?,1,15,32)
        k = K.expand_dims(K.dot(inputs, self.Wx), 1) #shape=(?,15,1,32)
        if self.use_additive_bias:
            #h = scale*K.tanh(1./scale *(q + k + self.bh)) #只能q+k才会转化为shape=(?,15,15,32) 矩阵运算
            h = K.tanh(q + k + self.bh)
        else:
            #h = scale*K.tanh(1./scale *(q + k))
            h = K.tanh(q + k)

        # e_{t, t'} = W_a h_{t, t'} + b_a
        if self.use_attention_bias:
            e = K.reshape(K.dot(h, self.Wa) + self.ba, (batch_size, input_len, input_len))
        else:
            e = K.reshape(K.dot(h, self.Wa), (batch_size, input_len, input_len))
        return e

    def _call_multiplicative_emission(self, inputs):
        # e_{t, t'} = x_t^T W_a x_{t'} + b_a
        e = K.batch_dot(K.dot(inputs, self.Wa), K.permute_dimensions(inputs, (0, 2, 1)))
        if self.use_attention_bias:
            e += self.ba[0]
        return e

    def compute_output_shape(self, input_shape):
        output_shape = input_shape
        if self.return_attention:
            attention_shape = (input_shape[0], output_shape[1], input_shape[1])
            return [output_shape, attention_shape]
        return output_shape
        # return [output_shape,(output_shape[0],output_shape[1],output_shape[1]),(output_shape[0],output_shape[1],output_shape[1])]

    def compute_mask(self, inputs, mask=None):
        if self.return_attention:
            return [mask, None]
        return mask

    def _attention_regularizer(self, attention):
        batch_size = K.cast(K.shape(attention)[0], K.floatx())
        input_len = K.shape(attention)[-1]
        indices = K.expand_dims(K.arange(0, input_len), axis=0)
        diagonal = K.expand_dims(K.arange(0, input_len), axis=-1)
        eye = K.cast(K.equal(indices, diagonal), K.floatx())
        return self.attention_regularizer_weight * K.sum(K.square(K.batch_dot(
            attention,
            K.permute_dimensions(attention, (0, 2, 1))) - eye)) / batch_size

    def scaled_tanh(x, scale=5.):
        return scale * K.tanh(1./scale * x)
    @staticmethod
    def get_custom_objects():
        return {'SeqSelfAttention': SeqDistanceSelfAttention}