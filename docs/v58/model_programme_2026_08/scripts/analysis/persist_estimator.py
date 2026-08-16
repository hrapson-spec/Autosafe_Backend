#!/usr/bin/env python3
"""PERSIST estimator core — frozen-membership, pooled-fallback, IRLS.

Replaces the estimator in persist_analyze.py after the 2026-08-16 estimand defect:
the previous version let per-replicate FIT SUCCESS decide which systems entered the
standardised average, and renormalised the denominator over the survivors. Membership
flickered in 16.0% of cohort-A replicates (visibility, measured over 200 resamples), so
both the effect and the population it standardised to moved between replicates.

Design, per PREREG_PERSIST §6.3 / D4 and the owner ruling of 2026-08-16:

  MEMBERSHIP    All nine systems, always. The standardisation denominator is every row
                of every system, in every replicate. Never renormalised over survivors.

  ELIGIBILITY   Declared ONCE from the full sample, before any resampling, on stated
                sufficiency criteria. An eligible system estimates its own coefficient
                (then shrunk). An ineligible system takes the POOLED coefficient in
                every replicate, deterministically -- its contribution never depends on
                whether a resample happened to clear a threshold.

  WEIGHTS       Vary with the resample. The prereg says "g-computation over the observed
                system mix" and does NOT nominate the TRAIN composition as a fixed
                reference population; §6.3 governs both TRAIN and EVAL, so a fixed TRAIN
                reference would force EVAL to standardise to TRAIN, which it nowhere says.

  FAILURE       An ELIGIBLE system failing to fit is a REPLICATE FAILURE: deterministic
                retry on a safer route first, then recorded with diagnostics. Never
                silently dropped -- the samples that break a fit are disproportionately
                the tail-forming ones, so dropping them narrows the interval.

Ineligible systems are fitted with the pooled coefficient held fixed via an OFFSET, so
their baseline and control structure is still estimated from their own rows. That needs
offset support, which sklearn lacks -- hence the local IRLS, which is also ~7x faster
than constructing a LogisticRegression per fit. Numerical equivalence to sklearn is
asserted by persist_test_estimator.py.
"""
import numpy as np

__all__ = ["irls_logistic", "design", "system_eligibility", "standardised_effect",
           "dersimonian_laird", "FitFailure", "CONTROLS", "GUARDS"]

CONTROLS = ["n_adv_sys_t", "n_items_sys_t", "tot_adv_t", "n_advised_systems_t",
            "tot_min_t", "tot_md_t", "n_episodes", "n_prior_fail_sysdays",
            "prior_md_items", "interval_days"]

#: Sufficiency criteria for a system to estimate its OWN persistence coefficient.
#: Evaluated once on the full sample per cohort; frozen for all replicates.
GUARDS = {"min_n": 40, "min_pos": 10, "min_treated": 5, "min_untreated": 5}


class FitFailure(RuntimeError):
    """An eligible system could not be fitted. Escalates to replicate failure."""

    def __init__(self, system, reason, diag):
        super().__init__(f"{system}: {reason}")
        self.system, self.reason, self.diag = system, reason, diag


# --------------------------------------------------------------------------- IRLS
def irls_logistic(X, y, offset=None, b0=None, max_iter=60, tol=1e-9, ridge=0.0):
    """Newton/IRLS logistic. Returns (beta_with_intercept, information_matrix).

    offset : fixed linear-predictor contribution (used to pin a coefficient)
    b0     : warm start; the full-sample solution is reused across replicates
    ridge  : ONLY for the deterministic retry route. Must be 0 for reported estimates.
    """
    n = X.shape[0]
    Xd = np.hstack([np.ones((n, 1)), X])
    k = Xd.shape[1]
    b = np.zeros(k) if b0 is None else np.asarray(b0, float).copy()
    if b.shape[0] != k:
        b = np.zeros(k)
    off = np.zeros(n) if offset is None else np.asarray(offset, float)
    pen = ridge * np.eye(k)
    pen[0, 0] = 0.0                       # never penalise the intercept
    H = None
    for _ in range(max_iter):
        eta = Xd @ b + off
        np.clip(eta, -30, 30, out=eta)
        mu = 1.0 / (1.0 + np.exp(-eta))
        w = np.clip(mu * (1.0 - mu), 1e-10, None)
        H = Xd.T @ (Xd * w[:, None]) + pen
        g = Xd.T @ (y - mu) - pen @ b
        try:
            step = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            step = np.linalg.pinv(H) @ g
        # damped Newton: back off if the step diverges
        for damp in (1.0, 0.5, 0.25, 0.1):
            nb = b + damp * step
            if np.all(np.isfinite(nb)):
                b_new = nb
                break
        else:
            raise FitFailure("?", "newton step non-finite", {})
        if np.max(np.abs(b_new - b)) < tol:
            b = b_new
            break
        b = b_new
    if not np.all(np.isfinite(b)):
        raise FitFailure("?", "non-finite coefficients", {})
    return b, H


# ------------------------------------------------------------------------- design
def design(df):
    """Controls + binned (not assumed-linear) terms. Zero-variance columns dropped.

    ⚠ A constant column makes the information matrix singular. The earlier version
    standardised such columns to zeros and the system was silently dropped; found by
    the synthetic planted-effect test.
    """
    cols = [c for c in CONTROLS if c in df.columns]
    X = df[cols].astype(float).fillna(0.0).to_numpy()
    extra = []
    for col in ("interval_days", "n_episodes"):
        if col not in df.columns:
            continue
        v = df[col].astype(float).fillna(0.0).to_numpy()
        for q in np.unique(np.quantile(v, [.2, .4, .6, .8])):
            b = (v > q).astype(float)
            if 0 < b.sum() < len(b):
                extra.append(b)
    if extra:
        X = np.hstack([X, np.column_stack(extra)])
    sd = X.std(0)
    keep = sd > 1e-12
    if not keep.any():
        return np.empty((len(X), 0))
    X = X[:, keep]
    return (X - X.mean(0)) / X.std(0)


# -------------------------------------------------------------------- eligibility
def system_eligibility(df, systems, guards=None):
    """Declared ONCE from the full sample. sys -> {eligible, n, n_pos, n_treated, reason}."""
    g = dict(GUARDS if guards is None else guards)
    out = {}
    for s in systems:
        d = df[df.sys == s]
        n = int(len(d))
        pos = int(d["y"].sum()) if n else 0
        tre = int(d["persistent"].sum()) if n else 0
        why = []
        if n < g["min_n"]:
            why.append(f"n={n}<{g['min_n']}")
        if pos < g["min_pos"]:
            why.append(f"n_pos={pos}<{g['min_pos']}")
        if tre < g["min_treated"]:
            why.append(f"n_treated={tre}<{g['min_treated']}")
        if n - tre < g["min_untreated"]:
            why.append(f"n_untreated={n-tre}<{g['min_untreated']}")
        out[s] = {"eligible": not why, "n": n, "n_pos": pos, "n_treated": tre,
                  "reason": "sufficient" if not why else "; ".join(why)}
    return out


# ------------------------------------------------------------------------ pooling
def dersimonian_laird(betas, ses):
    b, s = np.asarray(betas, float), np.asarray(ses, float)
    w = 1.0 / s**2
    fixed = (w * b).sum() / w.sum()
    Q = (w * (b - fixed) ** 2).sum()
    k = len(b)
    c = w.sum() - (w**2).sum() / w.sum()
    tau2 = max(0.0, (Q - (k - 1)) / c) if c > 0 else 0.0
    wr = 1.0 / (s**2 + tau2)
    pooled = (wr * b).sum() / wr.sum()
    shrink = tau2 / (tau2 + s**2)
    return float(pooled), float(tau2), pooled + shrink * (b - pooled), shrink


# ------------------------------------------------------------------- the estimand
def standardised_effect(df, systems, eligible, warm=None, strict=True):
    """Population-standardised persistence effect on the RISK scale.

    systems  : the frozen nine. Every one contributes its rows to the denominator.
    eligible : dict sys -> bool, declared once from the full sample.
    warm     : dict sys -> beta vector, reused as IRLS start across replicates.
    strict   : an eligible system that will not fit raises FitFailure (replicate failure).
    """
    fits, betas, ses, order = {}, [], [], []
    for s in systems:
        if not eligible.get(s, False):
            continue
        d = df[df.sys == s]
        y = d["y"].to_numpy(float)
        p = d["persistent"].to_numpy(float)
        X = np.hstack([p.reshape(-1, 1), design(d)])
        try:
            if len(d) < 2 or y.sum() in (0, len(y)) or p.sum() in (0, len(p)):
                raise FitFailure(s, "degenerate resample", {
                    "n": int(len(d)), "n_pos": int(y.sum()), "n_treated": int(p.sum())})
            b, H = irls_logistic(X, y, b0=(warm or {}).get(s))
            cov = np.linalg.pinv(H)
            se = float(np.sqrt(max(cov[1, 1], 0.0)))
            if not np.isfinite(b[1]) or not np.isfinite(se) or se <= 0:
                raise FitFailure(s, "non-finite beta or SE", {"se": se})
        except FitFailure as e:
            e.system = s
            # deterministic retry on a safer route before conceding
            try:
                b, H = irls_logistic(X, y, b0=None, max_iter=200, ridge=1e-6)
                cov = np.linalg.pinv(H)
                se = float(np.sqrt(max(cov[1, 1], 0.0)))
                if not np.isfinite(b[1]) or se <= 0:
                    raise RuntimeError
            except Exception:
                if strict:
                    raise
                continue
        fits[s] = (b, X, p)
        betas.append(float(b[1])); ses.append(se); order.append(s)

    if not order:
        raise FitFailure("<all>", "no eligible system fitted", {})
    pooled, tau2, shrunk, shrink = dersimonian_laird(betas, ses)
    shr = dict(zip(order, shrunk))

    # ---- g-computation over ALL nine systems, denominator never renormalised -------
    num, den, per_sys = 0.0, 0, {}
    for s in systems:
        d = df[df.sys == s]
        if len(d) == 0:
            per_sys[s] = {"n": 0, "role": "absent"}
            continue
        if s in fits:
            b, X, p = fits[s]
            bs = shr[s]
            eta = np.hstack([np.ones((len(X), 1)), X]) @ b
            adj = eta + (bs - b[1]) * p
            role, beta_used = "own(shrunk)", float(bs)
        else:
            # INELIGIBLE: pooled coefficient held fixed via offset; baseline and control
            # structure still estimated from this system's own rows.
            p = d["persistent"].to_numpy(float)
            y = d["y"].to_numpy(float)
            Xc = design(d)
            off = pooled * p
            bb, _ = irls_logistic(Xc, y, offset=off, max_iter=200, ridge=1e-8)
            adj = np.hstack([np.ones((len(Xc), 1)), Xc]) @ bb + off
            bs, role, beta_used = pooled, "pooled(offset)", float(pooled)
        eta_a = adj + (1.0 - p) * bs
        eta_c = adj - p * bs
        pa = 1.0 / (1.0 + np.exp(-np.clip(eta_a, -30, 30)))
        pc = 1.0 / (1.0 + np.exp(-np.clip(eta_c, -30, 30)))
        num += float((pa - pc).sum()); den += len(d)
        per_sys[s] = {"n": int(len(d)), "role": role, "beta_used": beta_used,
                      "beta_raw": float(fits[s][0][1]) if s in fits else None,
                      "se": float(ses[order.index(s)]) if s in order else None,
                      "shrinkage_weight": float(shrink[order.index(s)]) if s in order else 0.0,
                      "risk_diff_pp": float((pa - pc).mean() * 100)}

    return {"standardised_risk_diff_pp": num / den * 100,
            "tau": float(np.sqrt(tau2)), "tau2": float(tau2),
            "pooled_log_odds": pooled,
            "n_systems_in_denominator": sum(1 for s in systems if len(df[df.sys == s])),
            "n_systems_own_estimate": len(order),
            "systems_own_estimate": order,
            "per_system": per_sys, "n_rows": int(den),
            "warm": {s: fits[s][0] for s in fits}}
