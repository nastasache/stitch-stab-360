# Third-Party Licenses & Software Notices

StitchStab 360 incorporates or bundles portions of several third-party open-source projects. This document acknowledges the respective authors and provides the full license texts.

---

## Summary of Third-Party Components

| Component | Path | License | Copyright Holder / Project |
| :--- | :--- | :--- | :--- |
| **Google Spatial Media** | `scripts/spatialmedia/` | Apache License 2.0 | Copyright (c) 2016 Google Inc. |
| **Three.js** | `js/three/` | MIT License | Copyright (c) 2010-2022 Three.js Authors |
| **Leaflet** | `js/leaflet/` | BSD 2-Clause License | Copyright (c) 2010-2023 Vladimir Agafonkin, (c) 2010-2011 CloudMade |
| **Outfit Font** | `assets/fonts/Outfit-*.woff2` | SIL Open Font License 1.1 | Copyright (c) 2021 The Outfit Project Authors |
| **Plus Jakarta Sans Font** | `assets/fonts/PlusJakartaSans-*.woff2` | SIL Open Font License 1.1 | Copyright (c) 2020 The Plus Jakarta Sans Project Authors |

---

## 1. Google Spatial Media

- **Path**: `scripts/spatialmedia/`
- **Upstream Repository**: https://github.com/google/spatial-media
- **License**: Apache License 2.0

```
Copyright 2016 Google Inc. All rights reserved.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```

---

## 2. Three.js

- **Path**: `js/three/`
- **Upstream Repository**: https://github.com/mrdoob/three.js
- **License**: MIT License

```
The MIT License

Copyright © 2010-2022 three.js authors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

---

## 3. Leaflet

- **Path**: `js/leaflet/`
- **Upstream Repository**: https://github.com/Leaflet/Leaflet
- **License**: BSD 2-Clause License

```
BSD 2-Clause License

Copyright (c) 2010-2023, Vladimir Agafonkin
Copyright (c) 2010-2011, CloudMade
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

---

## 4. Outfit & Plus Jakarta Sans Web Fonts

- **Path**: `assets/fonts/`
- **Upstream Repositories**:
  - Outfit: https://github.com/Outfitio/Outfit-Fonts
  - Plus Jakarta Sans: https://github.com/tokotype/PlusJakartaSans
- **License**: SIL Open Font License 1.1

```
SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007

Copyright (c) 2021 The Outfit Project Authors (https://github.com/Outfitio/Outfit-Fonts)
Copyright (c) 2020 The Plus Jakarta Sans Project Authors (https://github.com/tokotype/PlusJakartaSans)

Permission is hereby granted, free of charge, to any person obtaining a copy
of the Font Software, to use, study, copy, merge, embed, modify, redistribute,
and sell modified and unmodified copies of the Font Software, subject to the
conditions in the SIL Open Font License Version 1.1 (see assets/fonts/OFL.txt).
```

---

## 5. Algorithmic & Academic Attributions

The stabilization algorithms implemented in this repository are independent, clean-room Python implementations of published mathematical formulations and computer vision research:

* **Kopf 3D-2D 360° Video Stabilization** (`scripts/stabilize_kopf.py`):
  - *Academic Paper*: Johannes Kopf. 2016. *360° Video Stabilization*. ACM Transactions on Graphics (Proc. SIGGRAPH Asia 2016), Vol. 35, No. 6, Article 195. [DOI: 10.1145/2980179.2982405](https://doi.org/10.1145/2980179.2982405)
  - *Method*: Cube-map pyramidal Lucas-Kanade feature tracking, spherical keyframe rotation estimation via Kabsch SVD + RANSAC, Cauchy loss trajectory smoothing via L-BFGS-B, deformed-rotation jitter modeling, and smoothed rotation re-application.

* **L1 Optimal Camera Paths** (`scripts/stabilize_cinematic.py`):
  - *Academic Paper*: Matthias Grundmann, Vivek Kwatra, and Irfan Essa. 2011. *Auto-Directed Video Stabilization with L1 Optimal Camera Paths*. IEEE Conference on Computer Vision and Pattern Recognition (CVPR 2011). [DOI: 10.1109/CVPR.2011.5995525](https://doi.org/10.1109/CVPR.2011.5995525)
  - *Method*: L1-norm trajectory regularization on SO(3) Euler angles yielding cinematic tripod holds (zero-velocity) and smooth constant-speed pans (zero-acceleration) with analytical Jacobian optimization.

* **Travel-Direction & Heading Lock** (`scripts/stabilize_traveldir.py`):
  - *Method*: Dynamic camera heading alignment locking 360° yaw toward the forward direction of travel or target heading, with angular damping and deadband filtering.

* **Nonlinear Complementary Filter on SO(3)** (`utils/imu_fusion.py`):
  - *Academic Paper*: Robert Mahony, Tarek Hamel, and Jean-Michel Pflimlin. 2008. *Nonlinear Complementary Filters on the Special Orthogonal Group*. IEEE Transactions on Automatic Control, Vol. 53, No. 5, pp. 1203–1218. [DOI: 10.1109/TAC.2008.923738](https://doi.org/10.1109/TAC.2008.923738)
  - *Method*: Quaternion-based 6-axis IMU sensor fusion correcting gyroscope integration drift via accelerometer gravity vectors.

* **Kabsch SVD Rotation Tracking** (`scripts/stabilize_kabsch.py`):
  - *Academic Paper*: Wolfgang Kabsch. 1976. *A solution for the best rotation to relate two sets of vectors*. Acta Crystallographica Section A, 32(5), 922–923. [DOI: 10.1107/S0567739476001873](https://doi.org/10.1107/S0567739476001873)
  - *Method*: Closed-form SVD derivation of the optimal orthogonal rotation matrix minimizing root-mean-square deviation between matched spherical point sets.


