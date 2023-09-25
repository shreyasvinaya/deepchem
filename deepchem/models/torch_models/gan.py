"""Generative Adversarial Networks."""
import numpy as np
import torch
import torch.nn as nn
import time

# from deepchem.models.torch_models import layers
from deepchem.models.torch_models.torch_model import TorchModel


class GAN(nn.Module):
    """Implements Generative Adversarial Networks.

    A Generative Adversarial Network (GAN) is a type of generative model.  It
    consists of two parts called the "generator" and the "discriminator".  The
    generator takes random noise as input and transforms it into an output that
    (hopefully) resembles the training data.  The discriminator takes a set of
    samples as input and tries to distinguish the real training samples from the
    ones created by the generator.  Both of them are trained together.  The
    discriminator tries to get better and better at telling real from false data,
    while the generator tries to get better and better at fooling the discriminator.

    In many cases there also are additional inputs to the generator and
    discriminator.  In that case it is known as a Conditional GAN (CGAN), since it
    learns a distribution that is conditional on the values of those inputs.  They
    are referred to as "conditional inputs".

    Many variations on this idea have been proposed, and new varieties of GANs are
    constantly being proposed.  This class tries to make it very easy to implement
    straightforward GANs of the most conventional types.  At the same time, it
    tries to be flexible enough that it can be used to implement many (but
    certainly not all) variations on the concept.

    To define a GAN, you must create a subclass that provides implementations of
    the following methods:

    get_noise_input_shape()
    get_data_input_shapes()
    create_generator()
    create_discriminator()

    If you want your GAN to have any conditional inputs you must also implement:

    get_conditional_input_shapes()

    The following methods have default implementations that are suitable for most
    conventional GANs.  You can override them if you want to customize their
    behavior:

    create_generator_loss()
    create_discriminator_loss()
    get_noise_batch()

    This class allows a GAN to have multiple generators and discriminators, a model
    known as MIX+GAN.  It is described in Arora et al., "Generalization and
    Equilibrium in Generative Adversarial Nets (GANs)" (https://arxiv.org/abs/1703.00573).
    This can lead to better models, and is especially useful for reducing mode
    collapse, since different generators can learn different parts of the
    distribution.  To use this technique, simply specify the number of generators
    and discriminators when calling the constructor.  You can then tell
    predict_gan_generator() which generator to use for predicting samples.
    """

    def __init__(self,
                 noise_input_shape,
                 data_input_shape,
                 conditional_input_shape,
                 generator_fn,
                 discriminator_fn,
                 n_generators=1,
                 n_discriminators=1):
        """Construct a GAN.

        In addition to the parameters listed below, this class accepts all the
        keyword arguments from KerasModel.

        Parameters
        ----------
        noise_input_shape: tuple
            the shape of the noise input to the generator.  The first dimension
            (corresponding to the batch size) should be omitted.
        data_input_shape: list of tuple
            the shapes of the inputs to the discriminator.  The first dimension
            (corresponding to the batch size) should be omitted.
        conditional_input_shape: list of tuple
            the shapes of the conditional inputs to the generator and discriminator.
            The first dimension (corresponding to the batch size) should be omitted.
            If there are no conditional inputs, this should be an empty list.
        generator_fn: callable
            a function that returns a generator.  It will be called with no arguments.
            The returned value should be a nn.Module whose input is a list
            containing a batch of noise, followed by any conditional inputs.  The
            number and shapes of its outputs must match the return value from
            get_data_input_shapes(), since generated data must have the same form as
            training data.
        discriminator_fn: callable
            a function that returns a discriminator.  It will be called with no
            arguments.  The returned value should be a nn.Module whose input is a
            list containing a batch of data, followed by any conditional inputs.  Its
            output should be a one dimensional tensor containing the probability of
            each sample being a training sample.
        n_generators: int
            the number of generators to include
        n_discriminators: int
            the number of discriminators to include
        """
        super(GAN, self).__init__()
        self.n_generators = n_generators
        self.n_discriminators = n_discriminators
        self.noise_input_shape = noise_input_shape
        self.data_input_shape = data_input_shape
        self.conditional_input_shape = conditional_input_shape
        self.create_generator = generator_fn
        self.create_discriminator = discriminator_fn

        # Inputs
        # Noise Input
        self.noise_input = nn.Parameter(torch.empty(self.noise_input_shape))
        # Data Input
        self.data_inputs = [
            nn.Parameter(torch.ones(s)) for s in self.data_input_shape
        ]
        # Conditional Input
        self.conditional_inputs = [
            nn.Parameter(torch.empty(s)) for s in self.conditional_input_shape
        ]

        self.data_input_layers = []
        for idx, shape in enumerate(self.data_input_shape):
            self.data_input_layers.append(nn.Parameter(torch.empty(shape)))
        self.conditional_input_layers = []
        for idx, shape in enumerate(self.conditional_input_shape):
            self.conditional_input_layers.append(
                nn.Parameter(torch.empty(shape)))

        # Generators
        self.generators = nn.ModuleList()
        self.gen_variables = nn.ParameterList()
        for i in range(n_generators):
            generator = self.create_generator()
            self.generators.append(generator)
            self.gen_variables += list(generator.parameters())

        # Discriminators
        self.discriminators = nn.ModuleList()
        self.discrim_variables = nn.ParameterList()

        for i in range(n_discriminators):
            discriminator = self.create_discriminator()
            self.discriminators.append(discriminator)
            self.discrim_variables += list(discriminator.parameters())

    def forward(self, inputs):
        """Compute the output of the GAN.

        Parameters
        ----------
        inputs: list of Tensor
            the inputs to the GAN.  The first element must be a batch of noise,
            followed by any conditional inputs.

        Returns
        -------
        total_gen_loss: Tensor
            the total loss for the generator
        total_discrim_loss: Tensor
            the total loss for the discriminator
        """

        n_generators = self.n_generators
        n_discriminators = self.n_discriminators
        noise_input, data_input_layers, conditional_input_layers = inputs[
            0], inputs[1], inputs[2]

        # print("\n\n\nNoise Input", noise_input.shape)
        # print("Data Input", data_input_layers.shape)
        # print("Conditional Input", conditional_input_layers.shape)
        self.noise_input.data = noise_input
        self.conditional_input_layers = [conditional_input_layers]
        self.data_input_layers = [data_input_layers]
        # Forward pass through generators
        generator_outputs = [
            gen(_list_or_tensor([[noise_input] + self.conditional_input_layers
                                ])) for gen in self.generators
        ]

        # Forward pass through discriminators
        discrim_train_outputs = [
            self._call_discriminator(disc, self.data_input_layers, True)
            for disc in self.discriminators
        ]
        # for gen_output in generator_outputs:
        #     print(type(gen_output))
        #     break
        discrim_gen_outputs = [
            self._call_discriminator(disc, [gen_output], False)
            for disc in self.discriminators
            for gen_output in generator_outputs
        ]

        # Compute loss functions
        gen_losses = [
            self.create_generator_loss(d) for d in discrim_gen_outputs
        ]
        discrim_losses = [
            self.create_discriminator_loss(
                discrim_train_outputs[i],
                discrim_gen_outputs[i * self.n_generators + j])
            for i in range(self.n_discriminators)
            for j in range(self.n_generators)
        ]

        # Compute the weighted errors
        if n_generators == 1 and n_discriminators == 1:
            total_gen_loss = gen_losses[0]
            total_discrim_loss = discrim_losses[0]
            # print(total_gen_loss, total_discrim_loss)
            # print('inside GAN after loss if')
        else:
            # Create learnable weights for the generators and discriminators.

            gen_alpha = nn.Parameter(torch.ones(1, n_generators))
            gen_weights = nn.Parameter(torch.softmax(gen_alpha, dim=1))

            discrim_alpha = nn.Parameter(torch.ones(1, n_discriminators))
            discrim_weights = nn.Parameter(torch.softmax(discrim_alpha, dim=1))
            # print("discrim_weights", discrim_weights)

            # Compute the weighted errors

            discrim_weights_n = discrim_weights.view(-1, self.n_discriminators,
                                                     1)
            gen_weights_n = gen_weights.view(-1, 1, self.n_generators)

            weight_products = torch.mul(discrim_weights_n, gen_weights_n)
            weight_products = weight_products.view(
                -1, self.n_generators * self.n_discriminators)
            # print(weight_products.shape)
            stacked_gen_loss = torch.stack(gen_losses, axis=0)
            stacked_discrim_loss = torch.stack(discrim_losses, axis=0)
            # print("stacked_gen_loss", stacked_gen_loss.shape)
            # print("stacked_discrim_loss", stacked_discrim_loss.shape)
            total_gen_loss = torch.sum(stacked_gen_loss * weight_products)
            total_discrim_loss = torch.sum(stacked_discrim_loss *
                                           weight_products)
            # print("total_gen_loss", total_gen_loss.shape)
            # print("total_discrim_loss", total_discrim_loss.shape)
            # print(gen_alpha)

            self.gen_variables += [gen_alpha]
            # print("Gen Variables:",self.gen_variables)
            self.discrim_variables += [gen_alpha]
            # print("Discrim Variables:",self.discrim_variables)
            self.discrim_variables += [discrim_alpha]

            # Add an entropy term to the loss.

            entropy = -(
                torch.sum(torch.log(gen_weights)) / n_generators +
                torch.sum(torch.log(discrim_weights)) / n_discriminators)
            # print("Entropy", entropy)
            total_discrim_loss = total_discrim_loss + entropy

        return total_gen_loss, total_discrim_loss

    def _call_discriminator(self, discriminator, inputs, train):
        """Invoke the discriminator on a set of inputs.

        This is a separate method so WGAN can override it and also return the
        gradient penalty.
        """
        return discriminator(
            _list_or_tensor(inputs + self.conditional_input_layers))

    def get_noise_batch(self, batch_size):
        """Get a batch of random noise to pass to the generator.

        This should return a NumPy array whose shape matches the one returned by
        get_noise_input_shape().  The default implementation returns normally
        distributed values.  Subclasses can override this to implement a different
        distribution.
        """
        size = list(self.noise_input_shape)
        size = [batch_size] + size[1:]
        return np.random.normal(size=size)

    def create_generator_loss(self, discrim_output):
        """Create the loss function for the generator.

        The default implementation is appropriate for most cases.  Subclasses can
        override this if the need to customize it.

        Parameters
        ----------
        discrim_output: Tensor
            the output from the discriminator on a batch of generated data.  This is
            its estimate of the probability that each sample is training data.

        Returns
        -------
        A Tensor equal to the loss function to use for optimizing the generator.
        """
        # print("Discrim Output", discrim_output.shape)
        # print(type(discrim_output), discrim_output)
        return -torch.mean(torch.log(discrim_output + 1e-10))

    def create_discriminator_loss(self, discrim_output_train,
                                  discrim_output_gen):
        """Create the loss function for the discriminator.

        The default implementation is appropriate for most cases.  Subclasses can
        override this if the need to customize it.

        Parameters
        ----------
        discrim_output_train: Tensor
            the output from the discriminator on a batch of training data.  This is
            its estimate of the probability that each sample is training data.
        discrim_output_gen: Tensor
            the output from the discriminator on a batch of generated data.  This is
            its estimate of the probability that each sample is training data.

        Returns
        -------
        A Tensor equal to the loss function to use for optimizing the discriminator.
        """
        return -torch.mean(
            torch.log(discrim_output_train + 1e-10) +
            torch.log(1 - discrim_output_gen + 1e-10))

    def discrim_loss_fn_wrapper(self, outputs, labels, weights):
        """Wrapper around create_discriminator_loss for use with fit_generator.

        Parameters
        ----------
        outputs: Tensor
            the output from the discriminator on a batch of training data.  This is
            its estimate of the probability that each sample is training data.
        labels: Tensor
            the labels for the batch.  These are ignored.
        weights: Tensor
            the weights for the batch.  These are ignored.

        Returns
        -------
        the value of the discriminator loss function for this input.
        """
        discrim_output_train, discrim_output_gen = outputs
        return self.create_discriminator_loss(discrim_output_train,
                                              discrim_output_gen)
        # return F.binary_cross_entropy(outputs, labels)

    def gen_loss_fn_wrapper(self, outputs, labels, weights):
        """Wrapper around create_generator_loss for use with fit_generator.

        Parameters
        ----------
        outputs: Tensor
            the output from the discriminator on a batch of generated data.  This is
            its estimate of the probability that each sample is training data.
        labels: Tensor
            the labels for the batch.  These are ignored.
        weights: Tensor
            the weights for the batch.  These are ignored.

        Returns
        -------
        the value of the generator loss function for this input.
        """
        discrim_output_train, discrim_output_gen = outputs
        return self.create_generator_loss(discrim_output_gen)
        # return F.binary_cross_entropy(outputs, labels)


def _list_or_tensor(inputs):
    if len(inputs) == 1:
        return inputs[0]
    return inputs


class GANModel(TorchModel):
    """Model for Generative Adversarial Networks.

    <writeup on the Model>

    Examples
    --------
    <examples here>

    References
    ----------
    <references>
    """

    def __init__(self, n_generators=1, n_discriminators=1, **kwargs):
        """
        Parameters
        ----------
        n_generators: int
            the number of generators to include
        n_discriminators: int
            the number of discriminators to include
        """
        self.n_generators = n_generators
        self.n_discriminators = n_discriminators

        model = GAN(noise_input_shape=self.get_noise_input_shape(),
                    data_input_shape=self.get_data_input_shapes(),
                    conditional_input_shape=self.get_conditional_input_shapes(),
                    generator_fn=self.create_generator,
                    discriminator_fn=self.create_discriminator,
                    n_generators=n_generators,
                    n_discriminators=n_discriminators)
        self.gen_loss_fn = model.create_generator_loss
        self.discrim_loss_fn = model.create_discriminator_loss
        self.discrim_loss_fn_wrapper = model.discrim_loss_fn_wrapper
        self.gen_loss_fn_wrapper = model.gen_loss_fn_wrapper
        self.data_inputs = model.data_inputs
        self.conditional_inputs = model.conditional_inputs
        self.data_input_layers = model.data_input_layers
        self.conditional_input_layers = model.conditional_input_layers
        self.generators = model.generators
        self.discriminators = model.discriminators
        self.gen_variables = model.gen_variables
        self.discrim_variables = model.discrim_variables
        self.get_noise_batch = model.get_noise_batch

        # print(model.data_inputs[0])
        super(GANModel, self).__init__(model,
                                       loss=self.gen_loss_fn_wrapper,
                                       **kwargs)

    def get_noise_input_shape(self):
        """Get the shape of the generator's noise input layer.

        Subclasses must override this to return a tuple giving the shape of the
        noise input.  The actual Input layer will be created automatically.  The
        dimension corresponding to the batch size should be omitted.
        """
        raise NotImplementedError("Subclasses must implement this.")

    def get_data_input_shapes(self):
        """Get the shapes of the inputs for training data.

        Subclasses must override this to return a list of tuples, each giving the
        shape of one of the inputs.  The actual Input layers will be created
        automatically.  This list of shapes must also match the shapes of the
        generator's outputs.  The dimension corresponding to the batch size should
        be omitted.
        """
        raise NotImplementedError("Subclasses must implement this.")

    def get_conditional_input_shapes(self):
        """Get the shapes of any conditional inputs.

        Subclasses may override this to return a list of tuples, each giving the
        shape of one of the conditional inputs.  The actual Input layers will be
        created automatically.  The dimension corresponding to the batch size should
        be omitted.

        The default implementation returns an empty list, meaning there are no
        conditional inputs.
        """
        return []

    def create_generator(self):
        """Create and return a generator.

        Subclasses must override this to construct the generator.  The returned
        value should be a tf.keras.Model whose inputs are a batch of noise, followed
        by any conditional inputs.  The number and shapes of its outputs must match
        the return value from get_data_input_shapes(), since generated data must
        have the same form as training data.
        """
        raise NotImplementedError("Subclasses must implement this.")

    def create_discriminator(self):
        """Create and return a discriminator.

        Subclasses must override this to construct the discriminator.  The returned
        value should be a tf.keras.Model whose inputs are all data inputs, followed
        by any conditional inputs.  Its output should be a one dimensional tensor
        containing the probability of each sample being a training sample.
        """
        raise NotImplementedError("Subclasses must implement this.")

    def fit_gan(self,
                batches,
                generator_steps=1,
                max_checkpoints_to_keep=5,
                checkpoint_interval=1000,
                restore=False):
        """Train this model on data.

        Parameters
        ----------
        batches: iterable
            batches of data to train the discriminator on, each represented as a dict
            that maps Inputs to values.  It should specify values for all members of
            data_inputs and conditional_inputs.
        generator_steps: float
            the number of training steps to perform for the generator for each batch.
            This can be used to adjust the ratio of training steps for the generator
            and discriminator.  For example, 2.0 will perform two training steps for
            every batch, while 0.5 will only perform one training step for every two
            batches.
        max_checkpoints_to_keep: int
            the maximum number of checkpoints to keep.  Older checkpoints are discarded.
        checkpoint_interval: int
            the frequency at which to write checkpoints, measured in batches.  Set
            this to 0 to disable automatic checkpointing.
        restore: bool
            if True, restore the model from the most recent checkpoint before training
            it.
        """
        self._ensure_built()

        gen_train_fraction = 0.0
        discrim_error = 0.0
        gen_error = 0.0
        discrim_average_steps = 0
        gen_average_steps = 0
        time1 = time.time()

        for feed_dict in batches:
            # Every call to fit_generator() will increment global_step, but we only
            # want it to get incremented once for the entire batch, so record the
            # value and keep resetting it.

            global_step = self.get_global_step()

            # Train the discriminator.

            inputs = [self.get_noise_batch(self.batch_size)]
            # print("Noise Input", inputs[0].shape)
            # temp_noise = torch.randn(self.get_noise_input_shape())
            temp_data = []
            for shape in self.get_data_input_shapes():
                temp_data.append(
                    torch.randn(size=[self.batch_size] + list(shape[1:])))
            # print("Data Input", temp_data[0].shape)
            temp_cond = []
            for shape in self.get_conditional_input_shapes():
                temp_cond.append(
                    torch.randn(size=[self.batch_size] + list(shape[1:])))
            # temp_data = torch.randn(self.get_data_input_shapes()[0])
            # inputs=(temp_noise,temp_data,temp_cond)
            # print("\n\n\ninputs OG:", inputs)
            inputs += [*temp_data, *temp_cond]
            print([i.shape for i in inputs])
            # print(self.data_input_layers[0].shape)
            # for input in self.data_input_layers:
            #     print("Input: ", input.data)
            #     inputs.append(feed_dict[input])
            # for input in self.conditional_input_layers:
            #     inputs.append(feed_dict[input])
            discrim_error += self.fit_generator(
                [(inputs, [], [])],
                variables=self.discrim_variables,
                loss=self.discrim_loss_fn_wrapper,
                checkpoint_interval=0,
                restore=restore)
            restore = False
            discrim_average_steps += 1
            # print("calc Discrim Error")
            # Train the generator.

            if generator_steps > 0.0:
                gen_train_fraction += generator_steps
                while gen_train_fraction >= 1.0:
                    inputs = [self.get_noise_batch(self.batch_size)
                             ] + inputs[1:]
                    # print("insdie gen",[i.shape for i in inputs])
                    gen_error += self.fit_generator(
                        [(inputs, [], [])],
                        variables=self.gen_variables,
                        loss=self.gen_loss_fn_wrapper,
                        checkpoint_interval=0)
                    gen_average_steps += 1
                    gen_train_fraction -= 1.0
            self._global_step = global_step + 1

            # Write checkpoints and report progress.

            if discrim_average_steps == checkpoint_interval:
                self.save_checkpoint(max_checkpoints_to_keep)
                discrim_loss = discrim_error / max(1, discrim_average_steps)
                gen_loss = gen_error / max(1, gen_average_steps)
                print(
                    'Ending global_step %d: generator average loss %g, discriminator average loss %g'
                    % (global_step, gen_loss, discrim_loss))
                discrim_error = 0.0
                gen_error = 0.0
                discrim_average_steps = 0
                gen_average_steps = 0

        # Write out final results.

        if checkpoint_interval > 0:
            if discrim_average_steps > 0 and gen_average_steps > 0:
                discrim_loss = discrim_error / discrim_average_steps
                gen_loss = gen_error / gen_average_steps
                print(
                    'Ending global_step %d: generator average loss %g, discriminator average loss %g'
                    % (global_step, gen_loss, discrim_loss))
            self.save_checkpoint(max_checkpoints_to_keep)
            time2 = time.time()
            print("TIMING: model fitting took %0.3f s" % (time2 - time1))

    def predict_gan_generator(self,
                              batch_size=1,
                              noise_input=None,
                              conditional_inputs=[],
                              generator_index=0):
        """Use the GAN to generate a batch of samples.

        Parameters
        ----------
        batch_size: int
            the number of samples to generate.  If either noise_input or
            conditional_inputs is specified, this argument is ignored since the batch
            size is then determined by the size of that argument.
        noise_input: array
            the value to use for the generator's noise input.  If None (the default),
            get_noise_batch() is called to generate a random input, so each call will
            produce a new set of samples.
        conditional_inputs: list of arrays
            the values to use for all conditional inputs.  This must be specified if
            the GAN has any conditional inputs.
        generator_index: int
            the index of the generator (between 0 and n_generators-1) to use for
            generating the samples.

        Returns
        -------
        An array (if the generator has only one output) or list of arrays (if it has
        multiple outputs) containing the generated samples.
        """
        if noise_input is not None:
            batch_size = len(noise_input)
        elif len(conditional_inputs) > 0:
            batch_size = len(conditional_inputs[0])
        if noise_input is None:
            noise_input = self.get_noise_batch(batch_size)
        inputs = [noise_input]
        inputs += conditional_inputs
        inputs = [i.astype(np.float32) for i in inputs]
        inputs = [torch.from_numpy(i) for i in inputs]
        pred = self.generators[generator_index](_list_or_tensor(inputs))
        pred = pred.detach().numpy()
        return pred