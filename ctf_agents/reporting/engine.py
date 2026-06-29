"""Report generation and formatting for CTF challenge results."""

import json
from typing import Dict, List, Optional
from datetime import datetime
from pathlib import Path


def generate_base_report(
    challenge_name: str,
    challenge_description: str,
    discovered_flags: List[str],
    attempts: int,
    successful_commands: List,
    failed_commands: List,
    start_time: Optional[datetime],
    end_time: Optional[datetime],
    extra_fields: Optional[Dict] = None,
) -> Dict[str, any]:
    """Generate a structured CTF challenge report dict."""
    report = {
        "challenge_name": challenge_name,
        "challenge_description": challenge_description,
        "flags_found": discovered_flags,
        "total_attempts": attempts,
        "successful_commands": len(successful_commands),
        "failed_commands": len(failed_commands),
        "time_elapsed": (end_time - start_time).total_seconds() if end_time and start_time else 0,
        "solved": len(discovered_flags) > 0,
        "command_history": [cmd for cmd, _ in successful_commands + failed_commands],
    }
    if extra_fields:
        report.update(extra_fields)
    return report


def print_report(report: Dict[str, any], extra_lines_fn=None):
    """Print a formatted final report to console."""
    print("\n" + "=" * 70)
    print(" FINAL REPORT")
    print("=" * 70)
    print(f"Challenge: {report['challenge_name']}")
    if "target_url" in report:
        print(f"Target: {report['target_url']}")
    print(f"Status: {' SOLVED' if report['solved'] else '❌ UNSOLVED'}")
    print(f"Flags Found: {len(report['flags_found'])}")
    if report["flags_found"]:
        for flag in report["flags_found"]:
            print(f"   {flag}")
    print(f"Total Attempts: {report['total_attempts']}")
    print(f"Successful Commands: {report['successful_commands']}")
    print(f"Failed Commands: {report['failed_commands']}")
    if extra_lines_fn:
        extra_lines_fn(report)
    print(f"Time Elapsed: {report['time_elapsed']:.2f} seconds")
    print("=" * 70 + "\n")


def save_report(report: Dict[str, any], prefix: str = "ctf_report", output_dir: str = "./reports") -> str:
    """Save report as JSON file and return the path."""
    Path(output_dir).mkdir(exist_ok=True)
    name = report.get("challenge_name", "unknown").replace(" ", "_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = Path(output_dir) / f"{prefix}_{name}_{timestamp}.json"
    with open(filepath, "w") as f:
        json.dump(report, f, indent=2)
    print(f" Report saved to: {filepath}")
    return str(filepath)
