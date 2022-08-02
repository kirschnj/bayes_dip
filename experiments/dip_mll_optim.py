import os
from itertools import islice
import hydra
from omegaconf import DictConfig, OmegaConf
import torch
from torch.utils.data import DataLoader
from bayes_dip.utils.experiment_utils import get_standard_ray_trafo, get_standard_dataset
from bayes_dip.utils import PSNR, SSIM
from bayes_dip.dip import DeepImagePriorReconstructor, UNetReturnPreSigmoid
from bayes_dip.probabilistic_models import get_default_unet_gaussian_prior_dicts
from bayes_dip.probabilistic_models import NeuralBasisExpansion, LowRankObservationCov, ParameterCov, ImageCov, ObservationCov
from bayes_dip.marginal_likelihood_optim import marginal_likelihood_hyperparams_optim, LowRankPreC, weights_linearization, get_ordered_nn_params_vec

@hydra.main(config_path='hydra_cfg', config_name='config')
def coordinator(cfg : DictConfig) -> None:

    if cfg.use_double:
        torch.set_default_tensor_type(torch.DoubleTensor)

    dtype = torch.get_default_dtype()
    device = torch.device(('cuda:0' if torch.cuda.is_available() else 'cpu'))

    ray_trafo = get_standard_ray_trafo(cfg)
    ray_trafo.to(dtype=dtype, device=device)

    # data: observation, ground_truth, filtbackproj
    dataset = get_standard_dataset(
            cfg, ray_trafo, use_fixed_seeds_starting_from=cfg.seed,
            device=device)

    for i, data_sample in enumerate(islice(DataLoader(dataset), cfg.num_images)):
        if i < cfg.get('skip_first_images', 0):
            continue

        if cfg.seed is not None:
            torch.manual_seed(cfg.seed + i)  # for reproducible noise in simulate

        observation, ground_truth, filtbackproj = data_sample

        torch.save({'observation': observation, 'filtbackproj': filtbackproj, 'ground_truth': ground_truth},
                f'sample_{i}.pt')

        observation = observation.to(dtype=dtype, device=device)
        filtbackproj = filtbackproj.to(dtype=dtype, device=device)
        ground_truth = ground_truth.to(dtype=dtype, device=device)

        net_kwargs = {
                'scales': cfg.dip.net.scales,
                'channels': cfg.dip.net.channels,
                'skip_channels': cfg.dip.net.skip_channels,
                'use_norm': cfg.dip.net.use_norm,
                'use_sigmoid': cfg.dip.net.use_sigmoid,
                'sigmoid_saturation_thresh': cfg.dip.net.sigmoid_saturation_thresh}
        reconstructor = DeepImagePriorReconstructor(
                ray_trafo, torch_manual_seed=cfg.dip.torch_manual_seed,
                device=device, net_kwargs=net_kwargs)
        optim_kwargs = {
                'lr': cfg.dip.optim.lr,
                'iterations': cfg.dip.optim.iterations,
                'loss_function': cfg.dip.optim.loss_function,
                'gamma': cfg.dip.optim.gamma}
        recon = reconstructor.reconstruct(
                observation,
                filtbackproj=filtbackproj,
                ground_truth=ground_truth,
                recon_from_randn=cfg.dip.recon_from_randn,
                log_path=cfg.dip.log_path,
                optim_kwargs=optim_kwargs)
        torch.save(reconstructor.nn_model.state_dict(),
                f'dip_model_{i}.pt')

        print(f'DIP reconstruction of sample {i}')
        print('PSNR:', PSNR(recon[0, 0].cpu().numpy(), ground_truth[0, 0].cpu().numpy()))
        print('SSIM:', SSIM(recon[0, 0].cpu().numpy(), ground_truth[0, 0].cpu().numpy()))

        prior_assignment_dict, hyperparams_init_dict = get_default_unet_gaussian_prior_dicts(
                reconstructor.nn_model)
        parameter_cov = ParameterCov(
                reconstructor.nn_model,
                prior_assignment_dict,
                hyperparams_init_dict,
                device=device
        )
        neural_basis_expansion = NeuralBasisExpansion(
                nn_model=reconstructor.nn_model,
                nn_input=filtbackproj,
                ordered_nn_params=parameter_cov.ordered_nn_params,
                nn_out_shape=filtbackproj.shape,
        )
        image_cov = ImageCov(
                parameter_cov=parameter_cov,
                neural_basis_expansion=neural_basis_expansion
        )
        observation_cov = ObservationCov(
                trafo=ray_trafo,
                image_cov=image_cov,
                device=device
        )
        linearized_weights = None
        if cfg.mll_optim.use_linearized_weights:
            linearize_weights_optim_kwargs = {
                    'iterations': cfg.mll_optim.linearize_weights.iterations,
                    'lr': cfg.mll_optim.linearize_weights.lr,
                    'wd': cfg.mll_optim.linearize_weights.wd,
                    'gamma': cfg.dip.optim.gamma,
                    'simplified_eqn': cfg.mll_optim.linearize_weights.simplified_eqn,
                    'use_sigmoid': cfg.dip.net.use_sigmoid,
                    }
            neural_basis_expansion_no_sigmoid = neural_basis_expansion
            if cfg.dip.net.use_sigmoid:
                nn_model_no_sigmoid = UNetReturnPreSigmoid(reconstructor.nn_model)
                neural_basis_expansion_no_sigmoid = NeuralBasisExpansion(
                        nn_model=nn_model_no_sigmoid,
                        nn_input=filtbackproj,
                        ordered_nn_params=parameter_cov.ordered_nn_params,
                        nn_out_shape=filtbackproj.shape,
                )
            map_weights = torch.clone(get_ordered_nn_params_vec(parameter_cov))
            linearized_weights = weights_linearization(
                trafo=ray_trafo,
                neural_basis_expansion=neural_basis_expansion_no_sigmoid,
                map_weights=map_weights,
                observation=observation,
                ground_truth=ground_truth,
                optim_kwargs=linearize_weights_optim_kwargs,
            )
        low_rank_observation_cov = LowRankObservationCov(
                trafo=ray_trafo,
                image_cov=image_cov,
                low_rank_rank_dim=cfg.mll_optim.preconditioner.low_rank_rank_dim,
                oversampling_param=cfg.mll_optim.preconditioner.oversampling_param,
                vec_batch_size=cfg.mll_optim.preconditioner.vec_batch_size,
                device=device
        )
        low_rank_preconditioner = LowRankPreC(
                pre_con_obj=low_rank_observation_cov
        )
        marglik_optim_kwargs = {
                'iterations': cfg.mll_optim.iterations,
                'lr': cfg.mll_optim.lr,
                'num_probes': cfg.mll_optim.num_probes,
                'linear_cg': {
                    'preconditioner': low_rank_preconditioner,
                    'max_iter': cfg.mll_optim.linear_cg.max_iter,
                    'rtol': cfg.mll_optim.linear_cg.rtol,
                    'update_freq': cfg.mll_optim.linear_cg.update_freq,
                },
                'min_log_variance': cfg.mll_optim.min_log_variance,
                'include_predcp': cfg.mll_optim.include_predcp,
                'predcp': OmegaConf.to_object(cfg.mll_optim.predcp)
                }
        marginal_likelihood_hyperparams_optim(
                observation_cov=observation_cov,
                observation=observation,
                recon=recon,
                linearized_weights=linearized_weights,
                optim_kwargs=marglik_optim_kwargs,
                log_path='./',
        )
        torch.save(
                observation_cov.state_dict(),
                f'observation_cov_{i}.pt')


if __name__ == '__main__':
    coordinator()
