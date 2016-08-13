from __future__ import division
from __future__ import print_function
from __future__ import absolute_import
from .core import *


class Activation(SingleInputMixin, OneDimOutputMixin, Node):
    #The OneDimOutputMixin is not really appropriate
    #if the activation is applied to, eg, a 2D conv layer 
    #output, but it also doesn't hurt anything, so I am
    #just keeping it this way for now (it would just break
    #if you tried to call its functions for a layer that was
    #not actually one dimensional)

    def __init__(self, mxts_mode,
                       expo_upweight_factor=0,
                       **kwargs):
        self.mxts_mode = mxts_mode
        self.expo_upweight_factor = expo_upweight_factor
        super(Activation, self).__init__(**kwargs)

    def get_yaml_compatible_object_kwargs(self):
        kwargs_dict = super(Activation, self).\
                       get_yaml_compatible_object_kwargs()
        kwargs_dict['mxts_mode'] = self.mxts_mode
        kwargs_dict['expo_upweight_factor'] = self.expo_upweight_factor
        return kwargs_dict

    def _compute_shape(self, input_shape):
        return input_shape

    def _build_fwd_pass_vars(self):
        super(Activation, self)._build_fwd_pass_vars() 
        self._gradient_at_default_activation =\
         self._get_gradient_at_activation(self._get_default_activation_vars())

    def _get_gradient_at_default_activation_var(self):
        return self._gradient_at_default_activation

    def _build_activation_vars(self, input_act_vars):
        raise NotImplementedError()

    def _deeplift_get_scale_factor(self):
        input_diff_from_default = self._get_input_diff_from_default_vars()
        near_zero_contrib_mask = (B.abs(input_diff_from_default)\
                                       < NEAR_ZERO_THRESHOLD)
        far_from_zero_contrib_mask = 1-(1*near_zero_contrib_mask)
        #the pseudocount is to avoid division-by-zero for the ones that
        #we won't use anyway
        pc_diff_from_default = input_diff_from_default +\
                                            (1*near_zero_contrib_mask) 
        #when total contrib is near zero,
        #the scale factor is 1 (gradient; piecewise linear). Otherwise,
        #compute the scale factor. The pseudocount doesn't mess anything up
        #as it is only there to prevent division by zero for the cases where
        #the contrib is near zero.
        scale_factor = near_zero_contrib_mask*\
                        self._get_gradient_at_default_activation_var() +\
                       (far_from_zero_contrib_mask*\
                        (self._get_diff_from_default_vars()/
                          pc_diff_from_default))
        return scale_factor
        
    def _gradients_get_scale_factor(self):
        return self._get_gradient_at_activation(
                self._get_input_activation_vars())  
        
    def _get_mxts_increments_for_inputs(self):
        if (self.mxts_mode == MxtsMode.DeconvNet):
            #apply the given nonlinearity in reverse
            mxts = self._build_activation_vars(self.get_mxts())
        else:
            #all the other ones here are of the form:
            # scale_factor*self.get_mxts()
            if (self.mxts_mode == MxtsMode.DeepLIFT): 
                scale_factor = self._deeplift_get_scale_factor()
            elif (self.mxts_mode == MxtsMode.GuidedBackpropDeepLIFT):
                deeplift_scale_factor = self._deeplift_get_scale_factor() 
                scale_factor = deeplift_scale_factor*(self.get_mxts() > 0)
            elif (self.mxts_mode == MxtsMode.Gradient):
                scale_factor = self._gradients_get_scale_factor() 
            elif (self.mxts_mode == MxtsMode.GuidedBackprop):
                scale_factor = self._gradients_get_scale_factor()\
                                *(self.get_mxts() > 0)
            else: 
                raise RuntimeError("Unsupported mxts_mode: "
                                   +str(self.mxts_mode))
            #apply the exponential upweighting
            orig_mxts = scale_factor*self.get_mxts()
            unnorm_mxts = orig_mxts*B.pow(B.abs(self.get_mxts()),
                                          self.expo_upweight_factor)
            #apply a rescaling so the total contribs going through are the
            #same...note that this may not preserve the total contribution
            #when the multipliers from other layers are factored in. Mostly,
            #it is there to reduce numerical underflow
            mxts = self.normalise_mxts(orig_mxts=orig_mxts,
                                       unnorm_mxts=unnorm_mxts) 
        return mxts

    def normalise_mxts(self, orig_mxts, unnorm_mxts):
        #normalise unnorm_mxts so that the total contribs of input as
        #mediated through this layer remains the same as for orig_mxts
        #remember that there is a batch axis
        #first, let's reshape orig_mxts and unnorm_mxts to be 2d
        orig_mxts_flat = B.flatten_keeping_first(orig_mxts)
        unnorm_mxts_flat = B.flatten_keeping_first(unnorm_mxts)
        input_act_flat = B.flatten_keeping_first(
                         self._get_input_activation_vars())
        total_contribs_of_input_orig = B.sum(orig_mxts_flat*input_act_flat,
                                             axis=1)
        total_contribs_of_input_unnorm = B.sum(unnorm_mxts_flat*input_act_flat, 
                                               axis=1)
        rescaling = (total_contribs_of_input_orig/
                     (total_contribs_of_input_unnorm + 
                      NEAR_ZERO_THRESHOLD*\
                       (total_contribs_of_input_unnorm < NEAR_ZERO_THRESHOLD)))
        #unnorm_mxts.shape[0] gives info on the batch size
        broadcast_shape = [unnorm_mxts.shape[0]]\
                                  +([1]*len(self._shape[1:])) #remaining are 1s
        return unnorm_mxts*(B.reshape(rescaling, broadcast_shape))


    def _get_gradient_at_activation(self, activation_vars):
        """
            Return the gradients at a specific supplied activation
        """
        raise NotImplementedError()


class PReLU(Activation):

    def __init__(self, alpha=0.0, **kwargs):
        super(PReLU, self).__init__(**kwargs)
        self.alpha = alpha

    def _build_activation_vars(self, input_act_vars):
        to_return = B.relu(input_act_vars)
        negative_mask = (input_act_vars < 0)
        to_return = to_return + negative_mask*input_act_vars*self.alpha
        return to_return

    def _get_gradient_at_activation(self, activation_vars):
        to_return = (activation_vars <= 0)*self.alpha +\
                    (activation_vars > 0)*1.0
        return to_return


class ReLU(PReLU):

    def __init__(self, **kwargs):
        super(ReLU, self).__init__(alpha=0.0, **kwargs)


class Sigmoid(Activation):

    def _build_activation_vars(self, input_act_vars):
        return B.sigmoid(input_act_vars) 

    def _get_gradient_at_activation(self, activation_vars):
        return B.sigmoid_grad(activation_vars)


class Softmax(Activation):

    def _build_activation_vars(self, input_act_vars):
        return B.softmax(input_act_vars)

    def _get_gradient_at_activation(self, activation_vars):
        return 0#punting; this needs to have
                #same dims as activation_vars
                #B.softmax_grad(activation_vars)
