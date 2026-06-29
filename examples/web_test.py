#!/usr/bin/env python3
"""
Simple Test Script for WebCTFAgent
This tests the web-based CTF challenge solver
"""

import os
import sys
import time
import subprocess
from datetime import datetime

# Import from ctf_agents package
from ctf_agents.core.providers import OpenRouterProvider
from ctf_agents.core.connections import SSHTerminal
from ctf_agents.core.web_agent import WebCTFAgent


def check_prerequisites():
    """Check if everything is ready"""
    print(" Checking prerequisites...\n")
    
    issues = []
    
    # 1. Check API key
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key or len(api_key) < 20:
        issues.append(" API key not set or invalid")
        print("   Set with: $env:OPENROUTER_API_KEY='your-key'")
    else:
        print(" API key is configured")
    
    # 2. Check if webctf image exists
    result = subprocess.run(
        ["docker", "images", "-q", "webctf:latest"],
        capture_output=True,
        text=True
    )
    if not result.stdout.strip():
        issues.append(" webctf:latest image not found")
        print("   Build with: python run_web_ctf.py (option 1)")
    else:
        print(" webctf:latest image exists")
    
    # 3. Check if container is running
    result = subprocess.run(
        ["docker", "ps", "--filter", "name=web-ctf-sandbox", "--format", "{{.Names}}"],
        capture_output=True,
        text=True
    )
    if "web-ctf-sandbox" not in result.stdout:
        print("!!  web-ctf-sandbox container not running (will start it)")
    else:
        print("web-ctf-sandbox container is running")
    
    print()
    
    if issues:
        print(" Prerequisites not met:")
        for issue in issues:
            print(f"   {issue}")
        return False
    
    return True


def start_test_container():
    """Start a simple web CTF container for testing"""
    print("Starting test container...\n")
    
        # Stop and remove if exists
    subprocess.run(["docker", "stop", "web-ctf-test"], 
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["docker", "rm", "web-ctf-test"], 
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
    # Create a simple HTML file with a flag
    test_html = """<!DOCTYPE html>
<html>
<head><title>CTF Test Challenge</title></head>
<body>
    <h1>Welcome to CTF Test</h1>
    <p>Can you find the hidden flag?</p>
    <!-- TODO: Remove before production! flag{test_web_agent_works} -->
    <p>Good luck!</p>
</body>
</html>"""
    
    # Write to a temporary file
    with open("test_index.html", "w") as f:
        f.write(test_html)
    
    # Start container with volume mount
    try:
        # Get absolute path
        import os
        current_dir = os.path.abspath(".")
        test_file = os.path.join(current_dir, "test_index.html")
        
        # Start container
        cmd = [
            "docker", "run", "-d",
            "--name", "web-ctf-test",
            "-p", "2224:22",   # Different port to avoid conflicts
            "-p", "8082:80",   # Different web port
            "webctf:latest"
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        container_id = result.stdout.strip()[:12]
        print(f" Container started: {container_id}")
        
        # Copy our test HTML into the container
        print(" Copying test HTML file...")
        subprocess.run([
            "docker", "cp", "test_index.html", 
            "web-ctf-test:/var/www/ctf/index.html"
        ], check=True)
        
        # Clean up temp file
        os.remove("test_index.html")
        
        print(" Waiting for services to start (10 seconds)...")
        time.sleep(10)
        
        print(" Container ready!")
        print(f"   Web: http://localhost:8082")
        print(f"   SSH: localhost:2224")
        print()
        
        return True
        
    except subprocess.CalledProcessError as e:
        print(f" Failed to start container: {e}")
        print(f"   Error: {e.stderr}")
        return False
    except Exception as e:
        print(f" Unexpected error: {e}")
        return False


def run_web_agent_test():
    """Run the WebCTFAgent test"""
    print("="*70)
    print("  WEBCTFAGENT TEST")
    print("="*70)
    print()
    API_KEY = os.getenv("OPENROUTER_API_KEY", "")
    
    # Get API key
    api_key = API_KEY
    # Configuration for test
    config = {
        "challenge_name": "HTML Comment Flag Test",
        "challenge_description": "Find the flag hidden in an HTML comment",
        "web_url": "http://localhost",  # The container's internal localhost
        "flag_format": r"flag\{[^\}]+\}",
        "ssh_host": "localhost",
        "ssh_port": 2224,  # Our test port
        "ssh_user": "sandboxuser",
        "ssh_pass": "sandboxpass",
        "max_iterations": 10  # Shorter for quick test
    }
    
    print(f" Challenge: {config['challenge_name']}")
    print(f" Target: {config['web_url']}")
    print(f" Max Iterations: {config['max_iterations']}")
    print(f" Expected Flag: flag{{test_web_agent_works}}")
    print()
    
    try:
        # Create components
        print(" Creating AI provider and terminal connection...")
        ai_provider = OpenRouterProvider(
            api_key=API_KEY,
            model=None  # uses DEFAULT_MODEL from config
        )
        
        terminal = SSHTerminal(
            host=config["ssh_host"],
            port=config["ssh_port"],
            username=config["ssh_user"],
            password=config["ssh_pass"]
        )
        
        # Create WebCTFAgent
        agent = WebCTFAgent(
            ai_provider=ai_provider,
            terminal=terminal,
            challenge_name=config["challenge_name"],
            challenge_description=config["challenge_description"],
            web_url=config["web_url"],
            flag_format=config["flag_format"]
        )
        
        print("Agent created successfully")
        print()
        
        # Run the agent
        print(" Starting AI agent...")
        print("="*70)
        
        result = agent.run(max_iterations=config["max_iterations"])
        
        # Analyze results
        print()
        print("="*70)
        print("  TEST RESULTS")
        print("="*70)
        
        expected_flag = "flag{test_web_agent_works}"
        
        if result['solved'] and expected_flag in result['flags_found']:
            print(" TEST PASSED!")
            print(f"   Flag found: {result['flags_found'][0]}")
            print(f"   Time: {result['time_elapsed']:.2f}s")
            print(f"   Commands: {result['total_attempts']}")
            success = True
        elif result['solved']:
            print("  TEST PARTIAL SUCCESS")
            print(f"   Flag found: {result['flags_found']}")
            print(f"   But expected: {expected_flag}")
            success = False
        else:
            print(" TEST FAILED")
            print("   No flag found")
            success = False
        
        print("="*70)
        
        return success, result
        
    except Exception as e:
        print(f"\n Test error: {e}")
        import traceback
        traceback.print_exc()
        return False, None


def cleanup_test_container():
    """Stop and remove test container"""
    print("\n🧹 Cleaning up test container...")
    
    subprocess.run(["docker", "stop", "web-ctf-test"], 
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["docker", "rm", "web-ctf-test"], 
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    print("Cleanup complete")


def main():
    print("\n" + "="*70)
    print("  SIMPLE WEBCTFAGENT TEST")
    print("="*70)
    print("\nThis will:")
    print("  1. Check prerequisites")
    print("  2. Start a test container with a simple challenge")
    print("  3. Run the WebCTFAgent to solve it")
    print("  4. Clean up\n")
    
    input("Press Enter to start...")
    print()
    
    try:
        # Step 1: Check prerequisites
        if not check_prerequisites():
            print("\nPlease fix the issues above before running the test")
            sys.exit(1)
        
        # Step 2: Start test container
        if not start_test_container():
            print("\n Failed to start test container")
            sys.exit(1)
        
        # Step 3: Run the agent
        success, result = run_web_agent_test()
        
        # Step 4: Cleanup
        cleanup_test_container()
        
        # Final summary
        print("\n" + "="*70)
        if success:
            print(" TEST SUCCESSFUL!")
            print("\nYour WebCTFAgent is working correctly!")
        else:
            print("  TEST COMPLETED WITH ISSUES")
            print("\nThe agent ran but didn't find the flag correctly.")
            print("This could be due to:")
            print("  • API rate limits")
            print("  • Model availability")
            print("  • Network issues")
            print("\nTry running again or check the logs above.")
        print("="*70)
        
        sys.exit(0 if success else 1)
        
    except KeyboardInterrupt:
        print("\n\n!! Test interrupted by user")
        cleanup_test_container()
        sys.exit(1)
    except Exception as e:
        print(f"\n Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        cleanup_test_container()
        sys.exit(1)

    report_file = f"ctf_report_web_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_file, 'w') as f:
        json.dump(result, f, indent=2)


if __name__ == "__main__":
    main()