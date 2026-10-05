// SPDX-License-Identifier: GPL-2.0-only
/*
 * Dummy ASoC Codec Driver
 *
 * Author: Your Name
 */

 #include <linux/init.h>
 #include <linux/module.h>
 #include <linux/platform_device.h>
 #include <sound/soc.h>
 
 static struct snd_soc_dai_driver dummy_codec_dai = {
     .name = "dummy-codec-am-hifi",
     .playback = {
         .channels_min = 2,
         .channels_max = 8,
         .stream_name = "Playback",
         .rates = SNDRV_PCM_RATE_8000_192000,
         .formats = SNDRV_PCM_FMTBIT_S16_LE |
                    SNDRV_PCM_FMTBIT_S24_LE |
                    SNDRV_PCM_FMTBIT_S32_LE
     },
 };
 
 static const struct snd_soc_dapm_widget dummy_codec_dapm_widgets[] = {
    SND_SOC_DAPM_DAC("DACL", NULL, SND_SOC_NOPM, 0, 0),
	SND_SOC_DAPM_DAC("DACR", NULL, SND_SOC_NOPM, 0, 0),

	SND_SOC_DAPM_OUTPUT("OUT_A"),
	SND_SOC_DAPM_OUTPUT("OUT_B"),
 };
 
 static const struct snd_soc_dapm_route dummy_codec_dapm_routes[] = {
    { "DACL",  NULL, "Playback" },
	{ "DACR",  NULL, "Playback" },

	{ "OUT_A", NULL, "DACL" },
	{ "OUT_B", NULL, "DACR" },
 };
 
 static const struct snd_soc_component_driver dummy_codec_component = {
     .dapm_widgets = dummy_codec_dapm_widgets,
     .num_dapm_widgets = ARRAY_SIZE(dummy_codec_dapm_widgets),
     .dapm_routes = dummy_codec_dapm_routes,
     .num_dapm_routes = ARRAY_SIZE(dummy_codec_dapm_routes),
 };
 
 static int dummy_codec_probe(struct platform_device *pdev)
 {
     return devm_snd_soc_register_component(&pdev->dev,
                                            &dummy_codec_component,
                                            &dummy_codec_dai, 1);
 }
 
 static const struct of_device_id dummy_codec_of_match[] = {
     { .compatible = "custom,dummy-codec-am", },
     {},
 };
 MODULE_DEVICE_TABLE(of, dummy_codec_of_match);
 
 static struct platform_driver dummy_codec_driver = {
     .probe = dummy_codec_probe,
     .driver = {
         .name = "dummy-codec-am",
         .of_match_table = dummy_codec_of_match,
     },
 };
 
 module_platform_driver(dummy_codec_driver);
 
 MODULE_DESCRIPTION("ASoC Dummy Codec AM Driver");
 MODULE_AUTHOR("Your Name");
 MODULE_LICENSE("GPL v2");
 