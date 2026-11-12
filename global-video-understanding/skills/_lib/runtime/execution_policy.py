"""Production policy gate; historical parameter decoding remains lossless."""


class ExecutionPolicyRetired(ValueError):
    code = "execution_policy_retired"


def require_active_policy(parameters):
    if (parameters or {}).get("execution_policy_version") == "efficient_v1":
        raise ExecutionPolicyRetired(
            "efficient_v1 is retired. Historical Case files are preserved; do not "
            "change their parameters or retry them with another policy. Create a "
            "new standard v3 Case without execution_policy_version, or select v2."
        )
