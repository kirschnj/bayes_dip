"""
Provides the kernelised sampling-based linearised NN inference routine for 
gprior hyperparameter, :func:``sample_based_marginal_likelihood_optim``.
"""
from typing import Dict
import os
import socket
import datetime
import torch
import numpy as np
import tensorboardX
from tqdm import tqdm
from torch import Tensor
from .sample_based_mll_optim_utils import (PCG_based_weights_linearization, 
                                           sample_then_optimise,
    estimate_effective_dimension, gprior_variance_mackay_update,
    debugging_loglikelihood_estimation, debugging_histogram_tensorboard, 
    debugging_uqviz_tensorboard
    )
from bayes_dip.utils import get_mid_slice_if_3d
from bayes_dip.utils import PSNR, SSIM, normalize
from bayes_dip.inference import SampleBasedPredictivePosterior
from bayes_dip.marginal_likelihood_optim import weights_linearization

def sample_based_marginal_likelihood_optim(
    predictive_posterior: SampleBasedPredictivePosterior,
    map_weights: Tensor, 
    observation: Tensor,
    nn_recon: Tensor,
    ground_truth: Tensor,
    optim_kwargs: Dict,
    log_path: str = './'
    ):

    '''
    Kernelised sampling-based linearised NN inference.
    ``sample_based_marginal_likelihood_optim`` implements Algo. 3 
    in https://arxiv.org/abs/2210.04994.
    '''

    writer = tensorboardX.SummaryWriter(
        logdir=os.path.join(log_path, '_'.join((
            datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
            socket.gethostname(),
            'marginal_likelihood_sample_based_hyperparams_optim')))
        )

    writer.add_image('nn_recon.', normalize(
        get_mid_slice_if_3d(nn_recon)[0]), 0)
    observation_cov = predictive_posterior.observation_cov
    with torch.no_grad():
        # s J (s^-1 map_weights)
        scale = observation_cov.image_cov.neural_basis_expansion.scale.pow(-1)
        scale_corrected_map_weights = scale*map_weights
        recon_offset = - nn_recon + observation_cov.image_cov.neural_basis_expansion.jvp(
            scale_corrected_map_weights[None, :]
        )
        observation_offset = observation_cov.trafo(recon_offset)
        observation_for_lin_optim = observation + observation_offset

        with tqdm(range(optim_kwargs['iterations']), desc='sample_based_marginal_likelihood_optim') as pbar:
            for i in pbar:
                # if False:
                # if optim_kwargs['use_cg_for_linearization']:
                
                # 
                # linearized_weights, linearized_observation, linearized_recon = PCG_based_weights_linearization(
                #     observation_cov=observation_cov, 
                #     observation=observation_for_lin_optim, 
                #     cg_kwargs=optim_kwargs['sample_kwargs']['cg_kwargs'],
                # )
                # print('cg : ', linearized_weights.pow(2).sum())
                # else:
                wd = 2. / observation_cov.image_cov.inner_cov.priors.gprior.log_variance.exp().detach()
                # wd *= 2. / observation.numel()
                optim_kwargs['sgd_kwargs'] = {
                    'simplified_eqn': True,
                    'iterations': int(1e4),
                    'lr': 5e-4,
                    'noise_precision': 1.,
                    'gamma': 0.,
                    'wd': wd}
                # exp(-w^T Sigma^-1 w) => -w^T Sigma^-1 w => 2 * Sigma^-1 w
                # gradiant of log -> (1 / variance_coeff) * w
                linearized_weights, linearized_recon = weights_linearization(
                    trafo=observation_cov.trafo, 
                    neural_basis_expansion=observation_cov.image_cov.neural_basis_expansion, 
                    use_sigmoid=False, 
                    map_weights=scale_corrected_map_weights, 
                    observation=observation_for_lin_optim, 
                    ground_truth=ground_truth, 
                    optim_kwargs=optim_kwargs['sgd_kwargs'])
                print('sgd : ', linearized_weights.pow(2).sum())
                linearized_observation = observation_cov.trafo.trafo(linearized_recon)
                
                
                
                # print('lin weights norm : ', (linearized_weights-linearized_weights_sgd).norm().item() / linearized_weights.norm().item())

                linearized_recon = linearized_recon - recon_offset.squeeze(dim=0)
                linearized_observation = linearized_observation - observation_offset
                # image_sample = f* + J z - J w_map
                # obs_sample = A * image_sample
                
                optim_kwargs['sample_then_optim_kwargs'] = {
                    'iterations': 1000,
                    'lr': 1e-3,
                    }
                weight_sample = sample_then_optimise(
                    observation_cov=observation_cov,
                    trafo=observation_cov.trafo,
                    neural_basis_expansion=observation_cov.image_cov.neural_basis_expansion, 
                    noise_variance=observation_cov.log_noise_variance.exp().detach(), 
                    variance_coeff=observation_cov.image_cov.inner_cov.priors.gprior.log_variance.exp().detach(), 
                    map_weights=map_weights,
                    ground_truth=ground_truth,
                    # num_samples=optim_kwargs['num_samples'], 
                    num_samples=8, 
                    linearized_weights=linearized_weights,
                    recon_offset=recon_offset,
                    optim_kwargs=optim_kwargs['sample_then_optim_kwargs'])

                image_samples = observation_cov.image_cov.neural_basis_expansion.jvp(weight_sample).squeeze(dim=1)

                # image_samples = predictive_posterior.sample_zero_mean(
                #     num_samples=optim_kwargs['num_samples'],
                #     **optim_kwargs['sample_kwargs']
                # )
                
                # print('image samples norm : ', (image_samples-image_samples_sto).norm().item() / image_samples.norm().item())
                obs_samples = observation_cov.trafo(image_samples)
                eff_dim = estimate_effective_dimension(posterior_obs_samples=obs_samples, noise_variance=observation_cov.log_noise_variance.exp().detach()).clamp(min=1, max=np.prod(observation_cov.trafo.obs_shape)-1)
                
                # obs_samples_sto = observation_cov.trafo(image_samples_sto)
                # eff_dim_sto = estimate_effective_dimension(posterior_obs_samples=obs_samples_sto, noise_variance=observation_cov.log_noise_variance.exp().detach()).clamp(min=1, max=np.prod(observation_cov.trafo.obs_shape)-1)

                variance_coeff = gprior_variance_mackay_update(
                    eff_dim=eff_dim, map_linerized_weights=linearized_weights
                )
                observation_cov.image_cov.inner_cov.priors.gprior.log_variance = variance_coeff.log()
                se_loss = (linearized_observation-observation).pow(2).sum()
                if optim_kwargs['cg_preconditioner'] is not None: optim_kwargs['cg_preconditioner'].update()
                
                torch.save(
                    observation_cov.state_dict(), 
                    f'observation_cov_iter_{i}.pt'
                )

                writer.add_scalar('variance_coeff', variance_coeff.item(), i)
                writer.add_scalar('noise_variance', observation_cov.log_noise_variance.data.exp().item(), i)
                writer.add_image('linearized_model_recon', normalize(get_mid_slice_if_3d(linearized_recon)[0]), i)
                writer.add_scalar('effective_dimension', eff_dim.item(), i)
                writer.add_scalar('se_loss', se_loss.item(), i)

                if optim_kwargs['activate_debugging_mode']:
                    loglik_nn_model, image_samples_diagnostic = debugging_loglikelihood_estimation(
                        predictive_posterior=predictive_posterior,
                        mean=get_mid_slice_if_3d(nn_recon),
                        ground_truth=get_mid_slice_if_3d(ground_truth),
                        image_samples=image_samples,
                        sample_kwargs=optim_kwargs['sample_kwargs'],
                        loglikelihood_kwargs=optim_kwargs['debugging_mode_kwargs']['loglikelihood_kwargs']
                    )
                    loglik_lin_model, _ = debugging_loglikelihood_estimation(
                        predictive_posterior=predictive_posterior,
                        mean=get_mid_slice_if_3d(linearized_recon),
                        ground_truth=get_mid_slice_if_3d(ground_truth),
                        image_samples=get_mid_slice_if_3d(image_samples_diagnostic),
                        loglikelihood_kwargs=optim_kwargs['debugging_mode_kwargs']['loglikelihood_kwargs']
                    )
                    writer.add_image('debugging_histogram_nn_model', debugging_histogram_tensorboard(
                        ground_truth, nn_recon, image_samples_diagnostic)[0], i)
                    writer.add_image('debugging_histogram_lin_model', debugging_histogram_tensorboard(
                        ground_truth, linearized_recon, image_samples_diagnostic)[0], i)
                    writer.add_image('debugging_histogram_uqviz_nn_model', debugging_uqviz_tensorboard(
                        ground_truth, nn_recon, image_samples_diagnostic)[0], i)
                    writer.add_scalar('loglik_nn_model',  loglik_nn_model.item(), i)
                    writer.add_scalar('loglik_lin_model', loglik_lin_model.item(), i)

                    if optim_kwargs['debugging_mode_kwargs']['verbose']:
                    
                        print('\n\033[1m' + f'iter: {i}, variance_coeff: {variance_coeff.item():.2E}, ',\
                            f'noise_variance: {observation_cov.log_noise_variance.data.exp().item():.2E}, ',\
                            f'eff_dim: {eff_dim.item():.2E}, se_loss: {se_loss.item():.2E} ',\
                            f'l2: {linearized_weights.pow(2).sum().item():.2E}' + '\033[0m')
                        print('\033[1m' + f'iter: {i}, linearized_recon PSNR: {PSNR(linearized_recon.cpu().numpy(), ground_truth.cpu().numpy()):.2E}, '\
                            f'SSIM: {SSIM(linearized_recon.cpu().numpy()[0, 0], ground_truth.cpu().numpy()[0, 0]):.2E}' + '\033[0m')
                        print('\033[1m' + f'iter: {i}, loglik_nn_model: {loglik_nn_model:.2E}, loglik_lin_model: {loglik_lin_model:.2E}\n' + '\033[0m')

    return linearized_weights, linearized_recon