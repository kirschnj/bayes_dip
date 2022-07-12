from typing import Optional, Callable, Dict
import torch
from torch import Tensor
from ..probabilistic_models import ObservationCov, LinearSandwichCov
from .random_probes import generate_probes_bernoulli
from xitorch import LinearOperator
from .xitorch_solvers import cg

class xitorch_operator(LinearOperator):
    def __init__(self, size, func, device='cpu', dtype=torch.float32):
        super().__init__(shape=(size,size), is_hermitian=True, device=device, dtype=dtype)
        self.func = func

    def _getparamnames(self, prefix=""):
       return []
    
    def _mv(self, v):
        return self.func(v)

def linear_cg(
        observation_cov: ObservationCov,
        v: Tensor,
        preconditioner: Optional[Callable] = None,
        max_cg_iter: int = 10,
        rtol: float = 1e-3 
        ) -> Tensor:

    num_probes = v.shape[-1]
    closure = xitorch_operator(
        size=observation_cov.trafo.obs_shape,
        func=lambda v: observation_cov(v.T.reshape(1, num_probes, *observation_cov.trafo.obs_shape)
            ).view(num_probes, observation_cov.shape[0]).T,
        device=observation_cov.device,
    )

    precond = None 
    if preconditioner is not None: 
        raise NotImplementedError
    else: 
        precond = None
    
    v_norm = torch.norm(v, 2, dim=0, keepdim=True)  
    v_scaled = v.div(v_norm)

    solve_T, residual_norm = cg(
        closure, v_scaled,
        posdef=True,
        precond=precond, 
        max_niter=max_cg_iter,
        rtol=rtol,
        atol=1e-08,
        eps=1e-6,
        resid_calc_every=10, 
        verbose=True
    )
    
    solve = solve_T.T * v_norm
    return solve, residual_norm

def approx_observation_cov_log_det_grads(
        observation_cov: ObservationCov,
        preconditioner=None,
        num_probes=1,
        ) -> Dict[Tensor]:
    """
    Estimates the gradient for the log-determinant ``log|observation_cov|`` w.r.t. its parameters
    via Hutchinson's trace estimator
    ``E(v.T @ observation_cov**-1 @ d observation_cov / d params @ v)``,
    with ``v.T @ observation_cov**-1`` being approximated by the conjugate gradient (CG) method.
    """
    trafo = observation_cov.trafo
    image_cov = observation_cov.image_cov
    log_noise_variance = observation_cov.log_noise_variance

    parameters = list(observation_cov.parameters())
    image_cov_parameters = list(image_cov.parameters())
    assert len(parameters) == len(image_cov.parameters()) + 1  # log_noise_variance

    assert isinstance(image_cov, LinearSandwichCov)
    # image_cov == image_cov.lin_op @ image_cov.inner_cov @ image_cov.lin_op_transposed
    # => d image_cov / d params ==
    #    image_cov.lin_op @ d image_cov.inner_cov / d params @ image_cov.lin_op_transposed

    v_flat = generate_probes_bernoulli(
            side_length=observation_cov.shape[0],
            num_probes=num_probes,
            device=observation_cov.device,
            jacobi_vector=None)  # (obs_numel, num_probes)

    grads = {}

    ## gradients for parameters in image_cov

    v_obs_left_flat, residual_norm = linear_cg(observation_cov, v_flat, preconditioner=preconditioner)    
    v_im_left_flat = trafo.trafo_adjoint_flat(v_obs_left_flat)  # (im_numel, num_probes)
    v_left = v_im_left_flat.T.reshape(1, num_probes, *trafo.im_shape)
    v_left = image_cov.lin_op_transposed(v_left)  # (num_probes, nn_params_numel)
    # v_left = v.T @ observation_cov**-1 @ trafo @ lin_op

    v_right = trafo.trafo_adjoint_flat(v_flat)
    v_right = v_right.T.reshape(1, num_probes, *trafo.im_shape)
    v_right = image_cov.lin_op_transposed(v_right)  # (num_probes, nn_params_numel)
    # v_right = lin_op_transposed @ trafo_adjoint @ v

    # estimate expected value E(v_left @ d image_cov.inner_cov / d params @ v_right.T)
    v_scalar = torch.sum(image_cov.inner_cov(v_left) * v_right, dim=1).mean()
    # (scalar product over network params)
    image_cov_grads = torch.autograd.grad((v_scalar,), image_cov_parameters)
    for param, grad in zip(image_cov_parameters, image_cov_grads):
        grads[param] = grad

    ## gradient for log_noise_variance

    # estimate expected value E(exp(v_obs_left_flat.T @ v_flat))
    noise_scalar = torch.sum(v_obs_left_flat * v_flat, dim=0).mean() * log_noise_variance.exp()
    # (scalar product over observation)
    grads[log_noise_variance] = torch.autograd.grad((noise_scalar,), (log_noise_variance,))[0]

    return grads
