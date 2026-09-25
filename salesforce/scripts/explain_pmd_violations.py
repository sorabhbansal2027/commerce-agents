"""
Reads a PMD SARIF report and calls Claude to explain each violation
in plain English with a suggested fix.
"""
import json
import os
import sys

import anthropic


def main(sarif_file: str) -> None:
    report = json.loads(open(sarif_file).read())

    violations = []
    for run in report.get("runs", []):
        for result in run.get("results", []):
            rule_id = result.get("ruleId", "unknown")
            message = result.get("message", {}).get("text", "")
            location = result.get("locations", [{}])[0]
            uri = location.get("physicalLocation", {}).get("artifactLocation", {}).get("uri", "")
            line = location.get("physicalLocation", {}).get("region", {}).get("startLine", "?")
            violations.append(f"- [{rule_id}] {uri}:{line} — {message}")

    if not violations:
        print("No PMD violations found in SARIF report")
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        for v in violations:
            print(v)
        return

    prompt = (
        "Explain each of these Salesforce PMD violations in plain English "
        "and suggest a concrete fix for each:\n\n" + "\n".join(violations[:20])
    )

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    print(message.content[0].text)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <sarif-file>")
        sys.exit(1)
    main(sys.argv[1])
