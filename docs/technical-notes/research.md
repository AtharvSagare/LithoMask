# Phase 0 - Research notes (LithoMask)
Status key: FULL = read full text; ABS = abstract/snippet only (re-read before relying on details).

## 1. Feng et al., Front. Mech. Eng. 2024, 19(4):24 (s11465-024-0795-1.pdf)  [FULL]
- Problem: OPC *model calibration* must capture pattern shift (misalignment of neighbouring holes), not only CD.
- Method: hybrid gauges (CD + space + optionally SEM-contour edge-placement); extra weight on the few space gauges in the cost (Eq.8);
  classical compact model fit by genetic algorithm; ML model used only as reference (overfitting risk with limited data).
- Result: pattern shift range within +-1 nm vs +-3.4 nm for CD-only model; full-field range ~9 nm -> ~5 nm.
- Reuse: aerial model (Eq.1-3), compact resist (Eq.4-6), weighted RMS (Eq.7-8), through-pitch test-structure idea, FOV >= 2x optical diameter.
- Not provided: the mask-correction algorithm.

## 2. Poonawala & Milanfar, IEEE TIP 16(3):774-788, 2007  [FULL, equations not extractable from PDF text -> TODO-VERIFY symbols]
- Pixel-based inverse OPC. Forward model: aerial image = mask convolved with PSF (Gaussian in the paper's demo), resist = sigmoid
  sig(u) = 1/(1+exp(-a(u-t))) (smooth stand-in for hard threshold, t = resist threshold, a = steepness).
- Cost F = squared L2 distance between target d and sigmoid output z. Mask relaxed to [0,1] via m = (1+cos(theta))/2, steepest descent on theta.
  Complexity O(N^2 log N) per gradient for N x N pixels (convolution dominated). Init from the target pattern converges fast (~200 iterations demo).
- Regularisation: quadratic penalty pushes m toward 0/1; total-variation penalty on the "activation pattern" (where m differs from target) suppresses
  isolated pixels and jagged edges. Weight mask W emphasises critical regions (e.g. zero weight near corners). "Selected parameter" mode restricts
  which pixels may change (e.g. 3-pixel band around edges).
- Extensions stated: coherent: z = sig(|h (x) m|^2); partially coherent via SOCS: I = sum_i lambda_i |h_i (x) m|^2.
- Limits stated: no mask-manufacturing constraints in formulation; binarisation needed; scalar/idealised model.
- Reuse (Phase 5C): exactly this formulation with SOCS kernels from Phase 3.

## 3. Ma & Arce, Opt. Express 19(3):2165-2180, 2011, "Pixel-based OPC optimization based on conjugate gradients"  [ABS]
- Replaces steepest descent with conjugate gradient (much faster convergence); partially coherent via sum of coherent systems;
  regularisation penalties for mask complexity; projection method to further reduce complexity. Companion: Ma & Arce, Computational Lithography (Wiley 2010).
- Reuse: CG option in Phase 5C. TODO: obtain full text (eecis.udel.edu/~arce/files/Publications/4-Pixelbased.pdf).

## 4. Edge-based (model-based, forward) OPC, Cobb & Zakhor 1995 lineage  [ABS via patents/reviews]
- Fragment polygon edges; simulate; EPE per fragment = distance between target and simulated contour; move fragment along its normal by
  (feedback factor x EPE); iterate until EPE below tolerance or movements converge. Feedback factor may depend on location/edge type.
- Classical weakness: neighbouring fragments interact (MEEF coupling) so naive per-fragment feedback converges poorly at small nodes -> damping,
  neighbour-aware or cross-MEEF updates (patent literature) ; model-based fragmentation length adaptation (Woo et al., SPIE 7640, 2010).
- Rule-based OPC applied first reduces iterations: hammerheads on line ends, corner serifs, bias.
- Reuse: Phase 5A (rules) and 5B (EPE loop, damping gain default 0.5-0.8, TODO tune).

## 5. SRAF  [ABS]
- Sub-resolution assist features (scatter bars) give isolated features a dense-like environment; trade-off: large/dense enough to help, small
  enough not to print. Rule-based and model-based variants; inserted before or during edge OPC.
- Reuse: Phase 5D rule-based first.

## 6. Level-set / DiffOPC alternatives  [ABS]
- Level-set ILT (Lv, Liu et al.; Shen) treats mask as continuum, removes small spurious blobs, can include defocus robustness.
- DiffOPC (arXiv 2408.08969): differentiable edge-based OPC, mask-rule-aware gradient on edge segments - bridges manufacturability of edge-based
  and accuracy of pixel-based. Candidate future upgrade after Phase 5.

## 7. Mask rule check (MRC)  [no dedicated source yet]
- TODO Phase 0b: find a source for typical MRC checks (min width, min space, min area, min jog, acute angle). For now implement as user-set numbers.

## Open questions
- Which wavelength/NA/illumination does the user's actual litho tool use? (needed for default process model). ASK USER.
- Is the target a photomask for contact aligner / stepper (projection ratio) or an e-beam-written mask only? Affects whether optical OPC is on the mask write or the wafer print.
- Feature scale (um or nm)? Drives pixel size and runtime.
