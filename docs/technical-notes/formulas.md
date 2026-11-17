# formulas.md - equations pinned for implementation
Source tags: [Feng24] = Feng et al. 2024 (read in full, equations transcribed from the extracted text; re-check against PDF layout);
[PM07] = Poonawala & Milanfar 2007 (equation bodies were stripped in text extraction -> structure from prose, TODO-VERIFY).

## Aerial image (Hopkins, partially coherent) [Feng24 Eq.1-3]
I(x,y) = sum over (f', f'') of TCC(f'_x,f'_y; f''_x,f''_y) * O^(f'_x,f'_y) * conj(O^(f''_x,f''_y)) * exp(2*pi*i*[(f'_x - f''_x)x + (f'_y - f''_y)y])
TCC(f', f'') = sum over (fx,fy) of J(fx,fy) * H(fx+f'_x, fy+f'_y) * conj(H(fx+f''_x, fy+f''_y))
  J = illumination (source) intensity distribution; H = imaging pupil; O^ = mask spectrum. Units: spatial frequency 1/nm; x,y nm.
SOCS: I(x,y) = sum_{i=1..N} lambda_i * | Phi_i(x,y) (x) O(x,y) |^2    (Eq.3)
  Phi_i orthogonal eigenfunctions of the TCC, lambda_i eigenvalues (descending), (x) = 2D convolution, N = kept kernels.
Implementation: choose N so that sum(lambda_1..N)/sum(lambda) >= 0.99 (default, tunable).

## Compact resist model [Feng24 Eq.4-6]
R(x,y) = sum_i c_i * M_i(x,y)
M_i = { [ grad^k (I -/+ b) ]^n (x) G_{s,p} }^(1/n)      k = derivative order, b = neutralisation constant (+ acid / - base),
  G_{s,p} = Gauss-Laguerre kernel order s, diffusion length p; n = exponent. Resist contour = level set of R at a threshold.
Typical 8-term example (Eq.6): R = c0*I + c1*(I+b1)(x)G_S1 + c2*(I+b2)(x)G_S2 + c3*(I-b3)(x)G_S3 + c4*|grad I|(x)G_S4
   + c5*[I^2 (x) G_S5]^0.5 + c6*[(I^2 + b6)(x) G_S6]^0.5 + c7*(laplacian I)(x)G_S7 + ...   [exact b-placement TODO-VERIFY against PDF]

## Calibration cost [Feng24 Eq.7-8]
RMS = sqrt( sum_i wt_i * e_i^2 / sum_i wt_i )                                  (Eq.7)
RMS_new = sqrt( sum_i wt_i*e_CD,i^2 / sum_i wt_i  +  wt_space * sum_j wt_j*e_SP,j^2 / sum_j wt_j )     (Eq.8)
  CD gauge error = simulated CD - wafer CD. EP gauge error = distance from simulated contour point to wafer contour point.
  Space gauges were few (13) vs CD gauges (~600) -> the extra weight wt_space balances them.

## Inverse (pixel) OPC [PM07, structure from prose; TODO-VERIFY constants]
Forward: z = sig( H m ),  sig(u) = 1/(1 + exp(-a (u - t))),  H = blur (PSF) matrix; partially coherent: replace H m by sum_i lambda_i |h_i (x) m|^2.
Cost: F(m) = || d - z ||_2^2 ; weighted: F_W = || W .* (d - z) ||^2.
Relaxation: m = (1 + cos(theta))/2 so 0 <= m <= 1, unconstrained theta, steepest descent theta <- theta - step * dF/dtheta.
Gradient by chain rule: dF/dm = (backpropagated) H^T [ -2 a (d - z) .* z .* (1 - z) ]  (derive ourselves & unit-test by finite differences).
Regularisers: R_binary = sum (m - something)^2 form that is zero at m in {0,1}; R_TV on activation pattern. Final: threshold m at 0.5.

## Edge-based OPC loop (generic, from industry description)
for each fragment f: EPE_f = signed distance (target edge -> simulated contour) along outward normal.
update: delta_f = -gain * EPE_f   (gain ~ 1/MEEF, default 0.5-0.8, tunable), clamp to max step, snap to grid, MRC check.
stop: max|EPE| < tol or iterations >= N_max or no improvement.

## Optics sanity formulas (use as test oracles)
Rayleigh: Res = k1 * lambda / NA.  Coherent cutoff frequency = NA/lambda.  Partially coherent dense-line cutoff pitch = lambda / (NA (1 + sigma)).
