import os
import hydra
import torch
import numpy as np

from gc import callbacks
from itertools import islice
from omegaconf import OmegaConf, DictConfig
from torch.utils.data import DataLoader
from hydra.utils import get_original_cwd

from bayes_dip.utils.experiment_utils import (
        get_standard_ray_trafo, get_standard_dataset, assert_sample_matches)
from bayes_dip.utils import PSNR, SSIM
from bayes_dip.dip import DeepImagePriorReconstructor
from bayes_dip.probabilistic_models import (
        get_default_unet_gaussian_prior_dicts, get_default_unet_gprior_dicts)
from bayes_dip.bayes_exp_design import (
    bed_optimal_angles_search, bed_eqdist_angles_baseline, BaseAnglesTracker, AcqStateTracker,
        plot_angles_callback, plot_obj_callback, get_hyperparam_fun_from_yaml, get_save_obj_callback)

@hydra.main(config_path='hydra_cfg', config_name='config', version_base='1.2')
def coordinator(cfg : DictConfig) -> None:

    if cfg.use_double:
        torch.set_default_tensor_type(torch.DoubleTensor)

    dtype = torch.get_default_dtype()
    device = torch.device(('cuda:0' if torch.cuda.is_available() else 'cpu'))
    ray_trafo_full, ray_trafo, subsampling_indices = get_standard_ray_trafo(
        cfg, return_full=True)
    ray_trafo_full.to(device=device)
    ray_trafo.to(device=device)

    # data: observation, ground_truth, filtbackproj
    dataset = get_standard_dataset(
            cfg, 
            ray_trafo,
            fold=cfg.dataset.fold, 
            use_fixed_seeds_starting_from=cfg.seed,
            ray_trafo_full=ray_trafo_full,
            subsampling_indices=subsampling_indices,
            device=device
        )

    for i, data_sample in enumerate(islice(DataLoader(dataset), cfg.num_images)): 
        
        if i < cfg.get('skip_first_images', 0):
            continue

        if cfg.seed is not None:
            torch.manual_seed(cfg.seed + i)  # for reproducible noise in simulate
        
        observation, ground_truth, filtbackproj, observation_full, filtbackproj_full = data_sample

        torch.save({
                'observation': observation, 'filtbackproj': filtbackproj,'ground_truth': ground_truth},
                f'sample_{i}.pt'
            )
        load_dip_params_from_path = cfg.load_dip_params_from_path
        if cfg.mll_optim.init_load_path is not None and load_dip_params_from_path is None:
            load_dip_params_from_path = cfg.mll_optim.init_load_path
        
        assert load_dip_params_from_path is not None 
        # assert that sample data matches with that from the dip to be loaded
        assert_sample_matches(
                (observation, ground_truth, filtbackproj),
                load_dip_params_from_path, i, raise_if_file_not_found=False)

        sample_dict = torch.load(
                os.path.join(get_original_cwd(), load_dip_params_from_path, f'sample_{i}.pt'), map_location='cpu')
        
        observation = observation.to(dtype=dtype, device=device)
        filtbackproj = filtbackproj.to(dtype=dtype, device=device)
        ground_truth = ground_truth.to(dtype=dtype, device=device)

        observation_full = observation_full.to(dtype=dtype, device=device)
        filtbackproj_full = filtbackproj_full.to(dtype=dtype, device=device)
        
        net_kwargs = {
                'scales': cfg.dip.net.scales,
                'channels': cfg.dip.net.channels,
                'skip_channels': cfg.dip.net.skip_channels,
                'use_norm': cfg.dip.net.use_norm,
                'use_sigmoid': cfg.dip.net.use_sigmoid,
                'sigmoid_saturation_thresh': cfg.dip.net.sigmoid_saturation_thresh}
        reconstructor = DeepImagePriorReconstructor(
                ray_trafo, torch_manual_seed=cfg.dip.torch_manual_seed,
                device=device, net_kwargs=net_kwargs,
                load_params_path=cfg.load_pretrained_dip_params
                )
        dip_params_filepath = os.path.join(load_dip_params_from_path, f'dip_model_{i}.pt')

        print(f'loading DIP network parameters from {dip_params_filepath}')
        reconstructor.load_params(dip_params_filepath)
        
        recon = reconstructor.nn_model(filtbackproj).detach()  # pylint: disable=not-callable
        init_state_dict = reconstructor.nn_model.state_dict()
        torch.save(
                reconstructor.nn_model.state_dict(),
                f'dip_model_{i}.pt'
            )
        torch.save(recon.cpu(), f'recon_{i}.pt')

        print(f'DIP reconstruction of sample {i}')
        print('PSNR:', PSNR(recon[0, 0].cpu().numpy(), ground_truth[0, 0].cpu().numpy()))
        print('SSIM:', SSIM(recon[0, 0].cpu().numpy(), ground_truth[0, 0].cpu().numpy()))
        
        angles_tracker = BaseAnglesTracker(
            ray_trafo=ray_trafo_full,
            angular_sub_sampling=cfg.trafo.angular_sub_sampling,
            total_num_acq_projs=cfg.acquisition.total_num_acq_projs,
            acq_projs_batch_size=cfg.acquisition.acq_projs_batch_size
            )

        if not cfg.compute_equidistant_baseline:

            prior_assignment_dict, hyperparams_init_dict = (
                    get_default_unet_gaussian_prior_dicts(reconstructor.nn_model)
                        if not cfg.priors.use_gprior else
                            get_default_unet_gprior_dicts(
                                reconstructor.nn_model,
                                {'variance': cfg.priors.gprior.init_prior_variance_value})
                    )
            
            acq_state_tracker = AcqStateTracker(
                angles_tracker=angles_tracker,
                nn_model=reconstructor.nn_model,
                observation_cov_kwargs={
                    'nn_input': filtbackproj,
                    'prior_assignment_dict': prior_assignment_dict, 
                    'hyperparams_init_dict': hyperparams_init_dict,
                    'use_gprior': cfg.priors.use_gprior,
                    'scale_kwargs': OmegaConf.to_object(cfg.priors.gprior.scale)
                },
                device=device
                )

            if cfg.mll_optim.init_load_path is not None:
                init_load_filepath = os.path.join(
                    cfg.mll_optim.init_load_path, f'observation_cov_{i}.pt')
                print(f'loading initial MLL hyperparameters from {init_load_filepath}')
                acq_state_tracker.observation_cov.load_state_dict(torch.load(init_load_filepath))

        hyperparam_fun = None
        if cfg.hyperparam_path_baseline is not None:
            hyperparam_fun = get_hyperparam_fun_from_yaml(
                    os.path.join(get_original_cwd(), cfg.hyperparam_path_baseline),
                    data=cfg.dataset.name,
                    noise_stddev=cfg.dataset.noise_stddev)

        tvadam_hyperparam_fun = None
        if cfg.use_alternative_recon == 'tvadam':
            assert cfg.tvadam_hyperparam_path_baseline is not None
            tvadam_hyperparam_fun = get_hyperparam_fun_from_yaml(
                    os.path.join(get_original_cwd(), cfg.tvadam_hyperparam_path_baseline),
                    data=cfg.dataset.name,
                    noise_stddev=cfg.dataset.noise_stddev)

        criterion = (
            'diagonal_EIG' if cfg.criterion.use_diagonal_EIG else 'EIG'
                                        ) if cfg.criterion.use_EIG else 'var'

        logged_plot_callbacks = {}
        logged_plot_callbacks['angles'] = plot_angles_callback
        if not cfg.compute_equidistant_baseline and cfg.use_best_inds_from_path is None:
            logged_plot_callbacks[criterion] = plot_obj_callback
        
        obj_list = []
        save_obj_callback = get_save_obj_callback(obj_list)
        callbacks = [save_obj_callback]

        if not cfg.compute_equidistant_baseline:
            use_precomputed_best_inds = None
            if cfg.use_best_inds_from_path is not None:
                use_precomputed_best_inds = np.concatenate(np.load(os.path.join(
                        cfg.use_best_inds_from_path,
                        f'bayes_exp_design_{i}.npz'))['best_inds_per_batch'])
        
            predcp_kwargs = OmegaConf.to_object(cfg.mll_optim.predcp)
            predcp_kwargs['gamma'] = cfg.dip.optim.gamma
            assert cfg.mll_optim.include_predcp is False
            marglik_optim_kwargs = {
                    'iterations': cfg.mll_optim.iterations,
                    'lr': cfg.mll_optim.lr,
                    'scheduler':{
                        'use_scheduler': cfg.mll_optim.scheduler.use_scheduler,
                        'step_size': cfg.mll_optim.scheduler.step_size,
                        'gamma': cfg.mll_optim.scheduler.gamma,
                        },
                    'num_probes': cfg.mll_optim.num_probes,
                    'linear_cg': {
                        'preconditioner': None,
                        'max_iter': cfg.mll_optim.linear_cg.max_iter,
                        'rtol': cfg.mll_optim.linear_cg.rtol,
                        'use_log_re_variant': cfg.mll_optim.linear_cg.use_log_re_variant,
                        'update_freq': cfg.mll_optim.linear_cg.update_freq,
                        'use_preconditioned_probes': cfg.mll_optim.linear_cg.use_preconditioned_probes
                        },
                    'min_log_variance': cfg.mll_optim.min_log_variance,
                    'include_predcp': cfg.mll_optim.include_predcp, # False
                    'predcp': predcp_kwargs
                }
            bed_kwargs = {
                    'acquisition': OmegaConf.to_object(cfg.acquisition),
                    'bayes_exp_design_inference': OmegaConf.to_object(cfg.bayes_exp_design_inference),
                    'use_precomputed_best_inds': use_precomputed_best_inds,
                    'use_gprior': cfg.priors.use_gprior,
                    'use_alternative_recon': cfg.use_alternative_recon,
                    'alternative_recon_kwargs': {
                        'tvadam_hyperparam_fun': tvadam_hyperparam_fun,
                        'tvadam_kwargs': OmegaConf.to_object(cfg.tvadam)
                        },
                    'log_path': './',
                    'marginal_lik_kwargs': marglik_optim_kwargs,
                    'update_preconditioner_kwargs': OmegaConf.to_object(cfg.mll_optim.linear_cg.preconditioner), 
                    'scale_update_kwargs': OmegaConf.to_object(cfg.priors.gprior.scale), 
                    'torch_manual_seed': cfg.dip.torch_manual_seed
                }
        
            best_inds, recons = bed_optimal_angles_search(
                acq_state_tracker=acq_state_tracker,
                init_state_dict=init_state_dict if cfg.init_eval_dip_from_load_path else None,
                observation_full=observation_full,
                filtbackproj=filtbackproj,
                ground_truth=ground_truth,
                criterion=criterion,
                bed_kwargs=bed_kwargs,
                dip_kwargs=OmegaConf.to_object(cfg.dip), # containing net and optim kwargs
                hyperparam_fun=hyperparam_fun,
                log_path=cfg.log_path,
                model_basename=f'refined_dip_model_{i}',
                callbacks=callbacks,
                logged_plot_callbacks=logged_plot_callbacks,
                device=device,
                dtype=dtype,
                )

            best_inds_per_batch = [
                    best_inds[j:j+cfg.acquisition.acq_projs_batch_size]
                    for j in range(0, cfg.acquisition.total_num_acq_projs, cfg.acquisition.acq_projs_batch_size)]
            print(f'angles to acquire (in this order, batch size {cfg.acquisition.acq_projs_batch_size})')
        else:
            recons = bed_eqdist_angles_baseline(
                ray_trafo_full=ray_trafo_full,
                angles_tracker=angles_tracker, # proj_inds_per_angle, init_angle_inds, acq_angle_inds, total_num_acq_projs, acq_projs_batch_size
                observation_full=observation_full,
                filtbackproj=filtbackproj,
                ground_truth=ground_truth,
                init_state_dict=init_state_dict if cfg.init_eval_dip_from_load_path else None,
                bed_kwargs = {
                    'reconstruct_every_k_step': cfg.acquisition.reconstruct_every_k_step,
                    'use_alternative_recon': cfg.use_alternative_recon,
                    'alternative_recon_kwargs': {
                        'tvadam_kwargs': OmegaConf.to_object(cfg.tvadam), 
                        'tvadam_hyperparam_fun': tvadam_hyperparam_fun,
                        }
                },
                dip_kwargs=OmegaConf.to_object(cfg.dip),
                hyperparam_fun=hyperparam_fun,
                logged_plot_callbacks=logged_plot_callbacks,
                model_basename='baseline_refined_dip_model_{}'.format(i),
                log_path=cfg.log_path,
                device=device
                )

        bayes_exp_design_dict = {}
        bayes_exp_design_dict['recons'] = recons
        bayes_exp_design_dict['reconstruct_every_k_step'] = cfg.acquisition.reconstruct_every_k_step
        bayes_exp_design_dict['ground_truth'] = ground_truth.cpu().numpy()[0, 0]
        bayes_exp_design_dict['obj_per_batch'] = obj_list

        if not cfg.compute_equidistant_baseline:
            bayes_exp_design_dict['best_inds_per_batch'] = best_inds_per_batch

        np.savez('./bayes_exp_design_{}.npz'.format(i), **bayes_exp_design_dict)

if __name__ == '__main__':
    coordinator()
