"""Evidence-bound linear feasibility and predicate-range verification.

This module never derives scene facts or trusts model-provided execution labels.
The compiler supplies linear motion constraints for an already selected path.
Results certify this mathematical model only, not a renderer or unseen paths.
"""
import hashlib
import json
import math
import time


REVISION = "facts_constraints_v1"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def finite(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("expected finite number")
    return float(value)


def solve(problem, frames, timeout_s=10):
    """Solve hard bounds then prove critical scalar ranges over the feasible set.

    Variables: id, lower, upper, hint, weight, unit (m, m/s, m/s2, s).
    Constraints: id, coefficients, lower/upper, evidence_frame_ids.
    Predicates: id, coefficients, lower/upper (accepted range).
    Explicit coverage flags are supplied by the deterministic compiler, never
    copied from a model candidate. Without full coverage no stable claim is made.
    """
    started = time.monotonic()
    try:
        budget = finite(timeout_s)
        if budget <= 0:
            raise ValueError("timeout must be positive")
        if problem.get("revision") != REVISION:
            return {"status": "unsupported", "reason": "contract_revision"}
        variables = problem["variables"]
        if not isinstance(variables, list) or not variables:
            raise ValueError("variables required")
        ids = [v["id"] for v in variables]
        if any(not isinstance(key, str) or not key for key in ids) or len(ids) != len(set(ids)):
            raise ValueError("duplicate or invalid variable ID")
        indexes = {key: i for i, key in enumerate(ids)}
        bounds, hints, weights = [], [], []
        for variable in variables:
            if variable["unit"] not in {"m", "m/s", "m/s2", "s"}:
                raise ValueError("unsupported variable unit")
            lo, hi = finite(variable["lower"]), finite(variable["upper"])
            hint, weight = finite(variable["hint"]), finite(variable["weight"])
            if lo > hi or weight <= 0:
                raise ValueError("invalid variable bounds or weight")
            bounds.append((lo, hi)); hints.append(hint); weights.append(weight)

        def row(item):
            coefficients = item["coefficients"]
            if not isinstance(coefficients, dict) or not coefficients or set(coefficients) - set(ids):
                raise ValueError("unknown or missing variable reference")
            result = [0.0] * len(ids)
            for key, value in coefficients.items():
                result[indexes[key]] = finite(value)
            return result

        matrix, rhs = [], []
        constraint_ids = set()
        for item in problem["constraints"]:
            if not isinstance(item["id"], str) or item["id"] in constraint_ids:
                raise ValueError("duplicate or invalid constraint ID")
            constraint_ids.add(item["id"])
            evidence = item["evidence_frame_ids"]
            if not isinstance(evidence, list) or not evidence or any(f not in frames for f in evidence):
                raise ValueError("missing or foreign evidence frame")
            for frame in evidence:
                finite(frames[frame]["time_s"])
            coefficients = row(item)
            if "lower" not in item and "upper" not in item:
                raise ValueError("constraint needs a bound")
            if "upper" in item:
                matrix.append(coefficients); rhs.append(finite(item["upper"]))
            if "lower" in item:
                matrix.append([-x for x in coefficients]); rhs.append(-finite(item["lower"]))
        preferences = problem.get('preferences', [])
        preference_ids=set()
        for item in preferences:
            if item['id'] in preference_ids: raise ValueError('duplicate preference ID')
            preference_ids.add(item['id']); row(item); finite(item['value'])
            if finite(item['weight'])<=0 or not item.get('evidence_frame_ids') or any(f not in frames for f in item['evidence_frame_ids']):
                raise ValueError('invalid estimate preference')
        predicates = problem.get("predicates", [])
        predicate_ids = set()
        for item in predicates:
            if item["id"] in predicate_ids:
                raise ValueError("duplicate predicate ID")
            predicate_ids.add(item["id"])
            row(item)
            if finite(item["lower"]) > finite(item["upper"]):
                raise ValueError("invalid predicate bounds")
        binding = digest({"problem": problem, "frames": frames})
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        return {"status": "incomplete", "reason": str(error)}

    try:
        from scipy.optimize import linprog
    except ImportError:
        return {"status": "unsupported", "reason": "solver_dependency_missing", "input_sha256": binding}

    def optimize(cost, a, b, limits):
        remaining = budget - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError("solver deadline")
        result = linprog(cost, A_ub=a or None, b_ub=b or None, bounds=limits,
                         method="highs", options={"time_limit": remaining})
        if result.status == 1:
            raise TimeoutError(result.message)
        return result

    try:
        n = len(ids)
        # Weighted L1 fit: |x-hint| <= z; hard constraints are never relaxed.
        slack_count=n+len(preferences)
        a = [r + [0.0] * slack_count for r in matrix]
        b = list(rhs)
        for i, hint in enumerate(hints):
            r = [0.0] * (n + slack_count); r[i] = 1; r[n + i] = -1
            a.append(r); b.append(hint)
            r = [0.0] * (n + slack_count); r[i] = -1; r[n + i] = -1
            a.append(r); b.append(-hint)
        for j,item in enumerate(preferences):
            coefficients=row(item)
            r=coefficients+[0.0]*slack_count; r[2*n+j]=-1
            a.append(r);b.append(item['value'])
            r=[-x for x in coefficients]+[0.0]*slack_count;r[2*n+j]=-1
            a.append(r);b.append(-item['value'])
        result = optimize([0.0] * n + weights + [i['weight'] for i in preferences], a, b, bounds + [(0, None)] * slack_count)
        if result.status == 2:
            return {"status": "infeasible", "input_sha256": binding,
                    "constraint_ids": sorted(constraint_ids)}
        if not result.success:
            return {"status": "incomplete", "reason": result.message, "input_sha256": binding}
        values = list(map(float, result.x[:n]))
        residual = max([0.0] + [sum(x*y for x, y in zip(r, values))-v for r, v in zip(matrix, rhs)]
                       + [lo-x for x, (lo, hi) in zip(values, bounds)]
                       + [x-hi for x, (lo, hi) in zip(values, bounds)])
        if residual > 1e-7:
            return {"status": "incomplete", "reason": "numerical_residual", "max_residual": residual}
        ranges, stable = [], True
        for predicate in predicates:
            coefficients = row(predicate)
            low = optimize(coefficients, matrix, rhs, bounds)
            high = optimize([-v for v in coefficients], matrix, rhs, bounds)
            if not low.success or not high.success:
                return {"status": "incomplete", "reason": "predicate_range_not_proven", "input_sha256": binding}
            lower, upper = float(low.fun), -float(high.fun)
            passed = lower >= predicate["lower"]-1e-7 and upper <= predicate["upper"]+1e-7
            stable = stable and passed
            ranges.append({"id": predicate["id"], "lower": lower, "upper": upper, "passed": passed})
        coverage = problem.get("coverage", {})
        complete = all(coverage.get(key) is True for key in (
            "evidence", "paths", "critical_predicates", "target_capability"))
        status = "incomplete" if not complete else "feasible_stable" if stable else "feasible_ambiguous"
        return {"status": status, "input_sha256": binding, "values": dict(zip(ids, values)),
                "predicate_ranges": ranges, "max_residual": residual,
                "estimate_residuals": [{"id":i["id"], "input_value":i["value"], "effective_value":sum(x*y for x,y in zip(row(i),values))} for i in preferences],
                "verification": "linear_model_only", "publish_authorized": False}
    except TimeoutError as error:
        return {"status": "timeout", "reason": str(error), "input_sha256": binding}
