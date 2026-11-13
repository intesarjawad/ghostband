# SheetSage2 extraction

SheetSage2 and MERT-v2 weights are CC-BY-NC-4.0, for non-commercial use only.

Each row includes a fresh model load, checksum verification, audio preparation and inference.
CPU and MPS use float32. MPS is experimental until every fixture matches CPU exactly.

| Fixture | Device | Audio s | Load s | Prepare s | Inference s | Total s | Ratio | >3x | Matches CPU |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |
| amazing-grace.wav | cpu | 30.000 | 4.105 | 0.009 | 30.408 | 34.522 | 1.151 | False | True |
| amazing-grace.wav | mps | 30.000 | 4.581 | 0.012 | 18.577 | 23.170 | 0.772 | False | True |
| greensleeves.wav | cpu | 30.000 | 4.438 | 0.017 | 30.600 | 35.056 | 1.169 | False | True |
| greensleeves.wav | mps | 30.000 | 4.745 | 0.009 | 22.462 | 27.217 | 0.907 | False | True |
| londonderry-air.wav | cpu | 30.000 | 4.164 | 0.011 | 30.910 | 35.085 | 1.170 | False | True |
| londonderry-air.wav | mps | 30.000 | 4.767 | 0.010 | 17.990 | 22.767 | 0.759 | False | True |
| ode-to-joy.wav | cpu | 30.000 | 4.058 | 0.011 | 27.540 | 31.608 | 1.054 | False | True |
| ode-to-joy.wav | mps | 30.000 | 4.934 | 0.012 | 16.639 | 21.584 | 0.719 | False | True |
| oh-susanna.wav | cpu | 30.000 | 4.173 | 0.013 | 35.959 | 40.145 | 1.338 | False | True |
| oh-susanna.wav | mps | 30.000 | 4.909 | 0.014 | 20.840 | 25.763 | 0.859 | False | True |

MPS comparison: passed
