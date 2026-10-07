# Qorgau research gaze model: provenance and scope

This notice accompanies the optional `public-gaze-v1` runtime profile. The
research gaze model uses training data from Gaze360 and MPIIFaceGaze.

Gaze360's Research License covers its database, models, and related source. It
limits use to research and direct research colleagues at the same institution
who adhere to its terms, and restricts copying and distribution to backup.
Its commercial-use restriction explicitly includes models trained on the dataset
and other derivatives. The complete terms are retained in
`Gaze360-Research-License.md`.

MPIIFaceGaze is supplied under CC BY-NC-SA 4.0 and described by its authors as
intended for non-commercial scientific purposes. Attribution, the restriction on
commercial use, and ShareAlike requirements for sharing adapted material are
set out in `MPIIFaceGaze-CC-BY-NC-SA-4.0.txt`.

The notices packaging step includes these texts and citations. Dataset images,
archives, and per-person annotations are separate from the packaged model weights
and are not copied by that step.

Please retain these citations for work based on the research data:

- Petr Kellnhofer, Adrià Recasens, Simon Stent, Wojciech Matusik, Antonio Torralba.
  **Gaze360: Physically Unconstrained Gaze Estimation in the Wild.** ICCV, 2019.
  https://doi.org/10.1109/ICCV.2019.00701
- Xucong Zhang, Yusuke Sugano, Mario Fritz, Andreas Bulling.
  **It’s Written All Over Your Face: Full-Face Appearance-Based Gaze Estimation.**
  CVPR Workshops, 2017, pp. 2299–2308.
  https://doi.org/10.1109/CVPRW.2017.284
- Bulling, Andreas, 2023, **MPIIFaceGaze**, DaRUS, V1.
  https://doi.org/10.18419/DARUS-3240

Full source citations and conditions are retained in `Gaze360-Dataset-Citation.md`
and `MPIIFaceGaze-Attribution.md`. Exact bundled weights are identified by the
adjacent `model-manifest.json`.
