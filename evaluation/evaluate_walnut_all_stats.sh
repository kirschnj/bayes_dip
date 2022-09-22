#!/bin/sh
yaml_root_path=../../../bayes_dip/scripts
# Bayes-DIP
mkdir -p $yaml_root_path/stats_walnut_sample_based_density
for patch_size in $(seq 1 10); do python evaluate_walnut_sample_based_density.py --runs_file $yaml_root_path/runs_walnut_sample_based_density/patch_size_$patch_size.yaml --save_to $yaml_root_path/stats_walnut_sample_based_density/patch_size_$patch_size.yaml; done
mkdir -p $yaml_root_path/stats_walnut_sample_based_density_add_image_noise_correction_term
for patch_size in $(seq 1 10); do python evaluate_walnut_sample_based_density.py --runs_file $yaml_root_path/runs_walnut_sample_based_density_add_image_noise_correction_term/patch_size_$patch_size.yaml --save_to $yaml_root_path/stats_walnut_sample_based_density_add_image_noise_correction_term/patch_size_$patch_size.yaml; done
mkdir -p $yaml_root_path/stats_walnut_sample_based_density_reweight_off_diagonal_entries
for patch_size in $(seq 1 10); do python evaluate_walnut_sample_based_density.py --runs_file $yaml_root_path/runs_walnut_sample_based_density_reweight_off_diagonal_entries/patch_size_$patch_size.yaml --save_to $yaml_root_path/stats_walnut_sample_based_density_reweight_off_diagonal_entries/patch_size_$patch_size.yaml; done
mkdir -p $yaml_root_path/stats_walnut_sample_based_density_add_image_noise_correction_term_reweight_off_diagonal_entries
for patch_size in $(seq 1 10); do python evaluate_walnut_sample_based_density.py --runs_file $yaml_root_path/runs_walnut_sample_based_density_add_image_noise_correction_term_reweight_off_diagonal_entries/patch_size_$patch_size.yaml --save_to $yaml_root_path/stats_walnut_sample_based_density_add_image_noise_correction_term_reweight_off_diagonal_entries/patch_size_$patch_size.yaml; done
# baselines
mkdir -p $yaml_root_path/stats_baseline_walnut_mcdo_density
for patch_size in $(seq 1 10); do python evaluate_baseline_walnut_mcdo_density.py --runs_file $yaml_root_path/runs_baseline_walnut_mcdo_density/patch_size_$patch_size.yaml --save_to $yaml_root_path/stats_baseline_walnut_mcdo_density/patch_size_$patch_size.yaml; done
mkdir -p $yaml_root_path/stats_baseline_walnut_mcdo_density_add_image_noise_correction_term
for patch_size in $(seq 1 10); do python evaluate_baseline_walnut_mcdo_density.py --runs_file $yaml_root_path/runs_baseline_walnut_mcdo_density_add_image_noise_correction_term/patch_size_$patch_size.yaml --save_to $yaml_root_path/stats_baseline_walnut_mcdo_density_add_image_noise_correction_term/patch_size_$patch_size.yaml; done
mkdir -p $yaml_root_path/stats_baseline_walnut_mcdo_density_reweight_off_diagonal_entries
for patch_size in $(seq 1 10); do python evaluate_baseline_walnut_mcdo_density.py --runs_file $yaml_root_path/runs_baseline_walnut_mcdo_density_reweight_off_diagonal_entries/patch_size_$patch_size.yaml --save_to $yaml_root_path/stats_baseline_walnut_mcdo_density_reweight_off_diagonal_entries/patch_size_$patch_size.yaml; done
mkdir -p $yaml_root_path/stats_baseline_walnut_mcdo_density_add_image_noise_correction_term_reweight_off_diagonal_entries
for patch_size in $(seq 1 10); do python evaluate_baseline_walnut_mcdo_density.py --runs_file $yaml_root_path/runs_baseline_walnut_mcdo_density_add_image_noise_correction_term_reweight_off_diagonal_entries/patch_size_$patch_size.yaml --save_to $yaml_root_path/stats_baseline_walnut_mcdo_density_add_image_noise_correction_term_reweight_off_diagonal_entries/patch_size_$patch_size.yaml; done
