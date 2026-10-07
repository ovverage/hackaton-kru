# Retained research notices

These text notices are tracked so a clean build can preserve attribution and
conditions without downloading datasets or consulting a developer's private
`data/` directory. `scripts/prepare_licenses.py` copies the five named research
notices into `dist/third-party` only for `runtime_gaze: public-gaze-v1`.
This inventory is not a permission to publish weights or datasets.

The following upstream texts were copied without editing their content from
the sources retained with the training data:

| Retained file | Original source |
| --- | --- |
| `Gaze360-Research-License.md` | https://raw.githubusercontent.com/erkil1452/gaze360/546762ef1373dae13569afdfbe501a834040e8c8/LICENSE.md |
| `Gaze360-Dataset-Citation.md` | https://raw.githubusercontent.com/erkil1452/gaze360/546762ef1373dae13569afdfbe501a834040e8c8/dataset/README.md |
| `MPIIFaceGaze-CC-BY-NC-SA-4.0.txt` | https://creativecommons.org/licenses/by-nc-sa/4.0/legalcode.txt |

`MPIIFaceGaze-Attribution.md` and `Research-Gaze-Notice.md` are Qorgau attribution
and scope notes, not modifications to the upstream licenses. The MPIIFaceGaze
dataset citation, license and scientific-use description are also recorded at
https://doi.org/10.18419/DARUS-3240 (DaRUS, version 1.0). They distinguish
generated model artifacts from source datasets and do not assign a new license
to the program or model weights.

Research model files, images, training archives, and private metadata are not
part of this directory.
