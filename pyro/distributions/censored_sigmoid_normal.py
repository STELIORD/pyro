import torch
from torch.distributions import constraints
from torch.distributions.transforms import SigmoidTransform

from pyro.distributions import TorchDistribution, Normal, TransformedDistribution
from pyro.util import is_bad


class CensoredSigmoidNormal(TorchDistribution):

    def __init__(self, loc, scale, upper_lim, lower_lim, validate_args=None):

        normal = Normal(loc, scale, validate_args=validate_args)
        self.transform = SigmoidTransform()
        self.base_dist = TransformedDistribution(normal, [self.transform])
        # Log-prob only computed correctly for univariate base distribution
        assert self.base_dist.event_dim == 0 or self.base_dist.event_dim == 1 and self.base_dist.event_shape[0] == 1
        self.upper_lim = upper_lim
        self.lower_lim = lower_lim

        super(CensoredSigmoidNormal, self).__init__(self.base_dist.batch_shape, self.base_dist.event_shape,
                                                    validate_args=validate_args)

    def z(self, value):
        return (self.transform.inv(value) - self.base_dist.base_dist.loc) / self.base_dist.base_dist.scale

    @constraints.dependent_property
    def support(self):
        raise NotImplemented

    def sample(self, sample_shape=torch.Size()):
        with torch.no_grad():
            x = self.base_dist.sample(sample_shape)
            x[x > self.upper_lim] = self.upper_lim
            x[x < self.lower_lim] = self.lower_lim
            return x

    def rsample(self, sample_shape=torch.Size()):
        x = self.base_dist.sample(sample_shape)
        x[x > self.upper_lim] = self.upper_lim
        x[x < self.lower_lim] = self.lower_lim

    def log_prob(self, value):
        """
        Scores the sample by giving a probability density relative to a new base measure.
        The new base measure places an atom at `self.upper_lim` and `self.lower_lim`, and
        has Lebesgue measure on the intervening interval.

        Thus, `log_prob(self.lower_lim)` and `log_prob(self.upper_lim)` represent probabilities
        as for discrete distributions. `log_prob(x)` in the interior represent regular
        pdfs with respect to Lebesgue measure on R.

        **Note**: `log_prob` scores from distributions with different censoring are not
        comparable.
        """
        log_prob = self.base_dist.log_prob(value)

        # To compute the log cdf, we use log(cdf), except where it would give -inf
        # In those cases we use an asymptotic formula log_prob(value) - value.abs().log()
        crit = 1e-40
        upper_cdf = 1. - self.base_dist.cdf(self.upper_lim)
        lower_cdf = self.base_dist.cdf(self.lower_lim)
        mask_upper = upper_cdf < crit
        mask_lower = lower_cdf < crit
        shape = self.base_dist.batch_shape
        asymptotic_upper = self.base_dist.log_prob(self.upper_lim.expand(shape)) - (crit+self.z(self.upper_lim).abs()).log()
        asymptotic_lower = self.base_dist.log_prob(self.lower_lim.expand(shape)) - (crit+self.z(self.lower_lim).abs()).log()
        upper_cdf[mask_upper] = 1.
        upper_cdf = upper_cdf.log()
        upper_cdf[mask_upper] = asymptotic_upper[mask_upper]
        lower_cdf[mask_lower] = 1.
        lower_cdf = lower_cdf.log()
        lower_cdf[mask_lower] = asymptotic_lower[mask_lower]
        if is_bad(upper_cdf):
            raise ArithmeticError("NaN in upper cdf {}".format(upper_cdf))
        if is_bad(lower_cdf):
            raise ArithmeticError("NaN in lower cdf {}".format(lower_cdf))

        # Fill in the log_prob as the log_cdf in appropriate places
        log_prob[value == self.upper_lim] = upper_cdf.expand_as(log_prob)[value == self.upper_lim]
        if is_bad(log_prob):
            print(value, is_bad(value))
            raise ArithmeticError("NaN in log_prob")
        log_prob[value > self.upper_lim] = float('-inf')
        if is_bad(log_prob):
            raise ArithmeticError("NaN in log_prob")
        log_prob[value == self.lower_lim] = lower_cdf.expand_as(log_prob)[value == self.lower_lim]
        if is_bad(log_prob):
            raise ArithmeticError("NaN in log_prob")
        log_prob[value < self.lower_lim] = float('-inf')
        if is_bad(log_prob):
            raise ArithmeticError("NaN in log_prob")

        return log_prob

    def cdf(self, value):
        if self._validate_args:
            self.base_dist._validate_sample(value)
        cdf = self.base_dist.cdf(value)
        cdf[value >= self.upper_lim] = 1.
        cdf[value < self.lower_lim] = 0.

    def icdf(self, value):
        # Is this even possible?
        raise NotImplemented
