#!/usr/bin/env python3
"""
Simple End-to-End Test:
1. Generate a CTF website using website_generator
2. Deploy it in a Docker container
3. Use WebCTFAgent to find the flag
"""

import os
import sys
import time
import subprocess
import json
import shutil
from pathlib import Path
from datetime import datetime

# Import from ctf_agents package
from ctf_agents.pcg.website_generator import CTFWebsiteGenerator
from ctf_agents.core.providers import OpenRouterProvider
from ctf_agents.core.connections import SSHTerminal
from ctf_agents.core.web_agent import WebCTFAgent


class SimpleE2ETest:
    """Simple end-to-end test orchestrator"""
    
    def __init__(self):
        self.api_key = os.getenv("OPENROUTER_API_KEY", "")
        self.container_name = "e2e-test-container"
        self.ssh_port = 2225  # Different port to avoid conflicts
        self.web_port = 8083
        self.website_gen = CTFWebsiteGenerator(output_dir="./test_websites")
        self.challenge_metadata = None
        self.website_path = None
    
    def step1_generate_website(self, challenge_type=None):
        """Step 1: Generate a CTF website"""
        print("="*70)
        print("  STEP 1: GENERATING CTF WEBSITE")
        print("="*70)
        
        # Generate website
        print(f"\n Generating {'random' if not challenge_type else challenge_type} challenge...")
        self.challenge_metadata = self.website_gen.generate_website(challenge_type)
        self.website_path = Path(self.challenge_metadata["website_path"])
        
        print(f" Website generated!")
        print(f"\n Challenge Details:")
        print(f"   Type: {self.challenge_metadata['challenge_type']}")
        print(f"   Flag: {self.challenge_metadata['flag']}")
        print(f"   Hint: {self.challenge_metadata['hint']}")
        print(f"   Path: {self.website_path}")
        print(f"   Files: {', '.join(self.challenge_metadata['files'])}")
        
        return True
    
    def step2_start_container(self):
        """Step 2: Start Docker container with the website"""
        print("\n" + "="*70)
        print("  STEP 2: DEPLOYING WEBSITE IN DOCKER")
        print("="*70)
        
        try:
            # Stop and remove if exists
            print("\n🧹 Cleaning up old containers...")
            subprocess.run(["docker", "stop", self.container_name], 
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["docker", "rm", self.container_name], 
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # Check if webctf image exists
            result = subprocess.run(
                ["docker", "images", "-q", "webctf:latest"],
                capture_output=True,
                text=True
            )
            
            if not result.stdout.strip():
                print(" webctf:latest image not found!")
                print("   Build it first: python run_web_ctf.py (option 1)")
                return False
            
            print(" webctf:latest image found")
            
            # Start container
            print(f"\n Starting container '{self.container_name}'...")
            cmd = [
                "docker", "run", "-d",
                "--name", self.container_name,
                "-p", f"{self.ssh_port}:22",
                "-p", f"{self.web_port}:80",
                "webctf:latest"
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            container_id = result.stdout.strip()[:12]
            print(f" Container started: {container_id}")
            
            # Copy website files to container
            print(f"\n Copying website files...")
            for file in self.website_path.iterdir():
                if file.name != "challenge.json":  # Don't copy metadata
                    print(f"   • {file.name}")
                    subprocess.run([
                        "docker", "cp", 
                        str(file), 
                        f"{self.container_name}:/var/www/ctf/{file.name}"
                    ], check=True, stdout=subprocess.DEVNULL)
            
            print("Files copied successfully")
            
            # Wait for services
            print(f"\n Waiting for services to start (10 seconds)...")
            time.sleep(10)
            
            # Verify SSH port
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            ssh_ready = s.connect_ex(('localhost', self.ssh_port)) == 0
            s.close()
            
            if ssh_ready:
                print("SSH service is ready")
            else:
                print("  SSH service may not be ready yet")
            
            print(f"\n Website deployed:")
            print(f"   Web: http://localhost:{self.web_port}")
            print(f"   SSH: localhost:{self.ssh_port}")
            
            return True
            
        except subprocess.CalledProcessError as e:
            print(f"\n Failed to start container: {e}")
            print(f"   Error: {e.stderr if e.stderr else 'Unknown error'}")
            return False
        except Exception as e:
            print(f"\nUnexpected error: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def step3_run_agent(self, max_iterations=15):
        """Step 3: Run WebCTFAgent to find the flag"""
        print("\n" + "="*70)
        print("  STEP 3: RUNNING AI AGENT TO FIND FLAG")
        print("="*70)
        
        try:
            # Create AI provider
            print("\n Initializing AI agent...")
            ai_provider = OpenRouterProvider(
                api_key=self.api_key,
                model=None  # uses DEFAULT_MODEL from config
            )
            
            # Create SSH terminal
            terminal = SSHTerminal(
                host="localhost",
                port=self.ssh_port,
                username="sandboxuser",
                password="sandboxpass"
            )
            
            # Create WebCTFAgent
            agent = WebCTFAgent(
                ai_provider=ai_provider,
                terminal=terminal,
                challenge_name=self.challenge_metadata['challenge_type'].replace('_', ' ').title(),
                challenge_description=self.challenge_metadata['hint'],
                web_url="http://localhost",  # Container's internal localhost
                flag_format=r"flag\{[^\}]+\}|CTF\{[^\}]+\}"
            )
        
            print(" Agent initialized")
            print(f" Challenge: {self.challenge_metadata['challenge_type']}")
            print(f" Expected Flag: {self.challenge_metadata['flag']}")
            print(f" Max Iterations: {max_iterations}")
            
            # Run the agent
            print("\n" + "-"*70)
            print(" Starting agent execution...")
            print("-"*70 + "\n")
            
            result = agent.run(max_iterations=max_iterations)
            
            return result
            
        except Exception as e:
            print(f"\nError running agent: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def step4_analyze_results(self, result):
        """Step 4: Analyze and display results"""
        print("\n" + "="*70)
        print("  STEP 4: ANALYZING RESULTS")
        print("="*70)
        
        if not result:
            print("\n No results to analyze (agent failed to run)")
            return False
        
        expected_flag = self.challenge_metadata['flag']
        found_flags = result.get('flags_found', [])
        
        print(f"\n Results Summary:")
        print(f"   Challenge Type: {self.challenge_metadata['challenge_type']}")
        print(f"   Expected Flag: {expected_flag}")
        print(f"   Found Flags: {found_flags}")
        print(f"   Attempts: {result.get('total_attempts', 0)}")
        print(f"   Time: {result.get('time_elapsed', 0):.2f}s")
        print(f"   Solved: {result.get('solved', False)}")
        
        # Check if correct flag was found
        success = expected_flag in found_flags
        
        print("\n" + "-"*70)
        if success:
            print(" SUCCESS! Agent found the correct flag!")
            print(f"   Flag: {expected_flag}")
            print(f"   Time taken: {result['time_elapsed']:.2f} seconds")
            print(f"   Commands executed: {result['total_attempts']}")
        else:
            print("FAILED! Agent did not find the correct flag")
            if found_flags:
                print(f"   Wrong flags found: {found_flags}")
            else:
                print("   No flags found at all")
            print(f"\n Expected Solution:")
            print(f"   {self.challenge_metadata['solution']}")
        print("-"*70)
        
        return success
    
    def cleanup(self):
        """Clean up Docker container"""
        print("\n" + "="*70)
        print("  CLEANUP")
        print("="*70)
        
        print(f"\n Stopping and removing container '{self.container_name}'...")
        subprocess.run(["docker", "stop", self.container_name], 
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["docker", "rm", self.container_name], 
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(" Container removed")
        
        # Ask about keeping website
        response = input("\n  Delete generated website? [y/N]: ").strip().lower()
        if response == 'y' and self.website_path and self.website_path.exists():
            shutil.rmtree(self.website_path)
            print(f"✅ Deleted {self.website_path}")
        else:
            print(f" Website kept at: {self.website_path}")
    
    def run_complete_test(self, challenge_type=None, max_iterations=15):
        """Run the complete end-to-end test"""
        print("\n" + "="*70)
        print("  SIMPLE END-TO-END TEST")
        print("  Website Generator → Docker → AI Agent")
        print("="*70)
        
        print(f"\n Test Configuration:")
        print(f"   Challenge Type: {challenge_type or 'Random'}")
        print(f"   Max Iterations: {max_iterations}")
        print(f"   Container: {self.container_name}")
        print(f"   Ports: SSH={self.ssh_port}, Web={self.web_port}")
        
        input("\nPress Enter to start the test...")
        
        try:
            # Step 1: Generate website
            if not self.step1_generate_website(challenge_type):
                print("\nFailed at Step 1")
                return False
            
            time.sleep(1)
            
            # Step 2: Deploy in Docker
            if not self.step2_start_container():
                print("\n Failed at Step 2")
                self.cleanup()
                return False
            
            time.sleep(1)
            
            # Step 3: Run agent
            result = self.step3_run_agent(max_iterations)
            report_file = f"ctf_report_Web_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(report_file, 'w') as f:
                json.dump(result, f, indent=2)
            print(f" Report saved to: {report_file}")
            # Step 4: Analyze results
            success = self.step4_analyze_results(result)
            
            # Cleanup
            self.cleanup()
            
            # Final summary
            print("\n" + "="*70)
            print("  FINAL SUMMARY")
            print("="*70)
            
            if success:
                print("\n END-TO-END TEST PASSED!")
                print("\n All components working correctly:")
                print("   • Website generation ✓")
                print("   • Docker deployment ✓")
                print("   • AI agent execution ✓")
                print("   • Flag discovery ✓")
            else:
                print("\n  END-TO-END TEST COMPLETED WITH ISSUES")
                print("\n  The agent didn't find the correct flag")
                print("   This could be due to:")
                print("   • API rate limits")
                print("   • Model limitations")
                print("   • Challenge too complex")
                print("   • Network issues")
            
            print("\n" + "="*70)
            
            return success
            
        except KeyboardInterrupt:
            print("\n\n  Test interrupted by user")
            self.cleanup()
            return False
        except Exception as e:
            print(f"\n Unexpected error: {e}")
            import traceback
            traceback.print_exc()
            self.cleanup()
            return False


def main():
    """Main entry point"""
    print("\n" + "="*70)
    print("  SIMPLE E2E TEST: WEBSITE GENERATOR + AI AGENT")
    print("="*70)
    
    print("\nThis test will:")
    print("  1. Generate a random CTF website")
    print("  2. Deploy it in a Docker container")
    print("  3. Run the AI agent to find the flag")
    print("  4. Verify the results")
    print("  5. Clean up")
    
    print("\n  Prerequisites:")
    print("  • Docker running")
    print("  • webctf:latest image built")
    print("  • API key configured")
    print("  • Ports 2225 and 8083 available")
    
    # Choose challenge type
    print("\n Available challenge types:")
    print("  1. hidden_comment (easiest)")
    print("  2. robots_txt")
    print("  3. source_code")
    print("  4. base64_encoded")
    print("  5. hidden_form_field")
    print("  6. Random (let the generator choose)")
    
    choice = input("\nSelect challenge type [1-6, default: 1]: ").strip()
    
    challenge_types = {
        '1': 'hidden_comment',
        '2': 'robots_txt',
        '3': 'source_code',
        '4': 'base64_encoded',
        '5': 'hidden_form_field',
        '6': None
    }
    
    challenge_type = challenge_types.get(choice, 'hidden_comment')
    
    # Create and run test
    test = SimpleE2ETest()
    success = test.run_complete_test(
        challenge_type=challenge_type,
        max_iterations=15
    )
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()