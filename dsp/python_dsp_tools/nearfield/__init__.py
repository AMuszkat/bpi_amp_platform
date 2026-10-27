"""Shared DSP core for the near-field measurement and tuning tools.

    nearfield.lib     -- the signal-processing library (sweeps, deconvolution, windowing,
                         NF+FF splice, crossover / correction filter design, C++ export)
    nearfield.config  -- machine-specific settings (config/local.env, see that module)

Both GUIs (measurement/near_field_gui.py and tuning/dsp_gui.py) import from here; neither
imports the other.
"""
