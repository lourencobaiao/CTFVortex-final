from faker import Faker
import random
import os
import json
from pathlib import Path
from typing import Dict, List, Tuple
import base64
import hashlib

fake = Faker()


class CTFWebsiteGenerator:
    """Generates random CTF challenge websites with hidden flags"""

    def __init__(self, output_dir: str = "./ctf_websites"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.fake = Faker()

        # CTF challenge types
        self.challenge_types = [
            "hidden_comment",
            "robots_txt",
            "hidden_page",
            "source_code",
            "base64_encoded",
            "cookie_flag",
            "http_header",
            "javascript_obfuscated",
            "hidden_form_field",
            "image_metadata"
        ]

    def generate_flag(self) -> str:
        """Generate a random CTF flag"""
        flag_formats = [
            f"flag{{{self.fake.uuid4()[:16]}}}",
            f"CTF{{{self.fake.bothify('????####').upper()}}}",
            f"flag{{{self.fake.word()}_{self.fake.random_number(digits=6)}}}",
        ]
        return random.choice(flag_formats)

    def generate_fake_company_data(self) -> Dict:
        """Generate fake company information"""
        return {
            "company": self.fake.company(),
            "tagline": self.fake.catch_phrase(),
            "description": self.fake.bs(),
            "address": self.fake.address().replace('\n', ', '),
            "phone": self.fake.phone_number(),
            "email": self.fake.company_email(),
            "ceo": self.fake.name(),
            "founded": self.fake.year(),
            "employees": random.randint(10, 5000)
        }

    def generate_hidden_comment(self, flag: str) -> str:
        """Challenge Type 1: Flag hidden in HTML comment"""
        company = self.generate_fake_company_data()

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{company['company']} - {company['tagline']}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 0; padding: 20px; background: #f4f4f4; }}
        .container {{ max-width: 1200px; margin: 0 auto; background: white; padding: 40px; border-radius: 8px; }}
        h1 {{ color: #333; }}
        .hero {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 60px 20px; text-align: center; border-radius: 8px; margin-bottom: 30px; }}
        .features {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 20px; margin-top: 30px; }}
        .feature {{ background: #f8f9fa; padding: 20px; border-radius: 8px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="hero">
            <h1>{company['company']}</h1>
            <p style="font-size: 1.5em;">{company['tagline']}</p>
        </div>

        <!-- TODO: Remove this before production! Developer notes: {flag} -->

        <h2>About Us</h2>
        <p>{self.fake.text(200)}</p>

        <h2>Our Services</h2>
        <div class="features">
            <div class="feature">
                <h3>{self.fake.bs()}</h3>
                <p>{self.fake.text(100)}</p>
            </div>
            <div class="feature">
                <h3>{self.fake.bs()}</h3>
                <p>{self.fake.text(100)}</p>
            </div>
            <div class="feature">
                <h3>{self.fake.bs()}</h3>
                <p>{self.fake.text(100)}</p>
            </div>
        </div>

        <h2>Contact Us</h2>
        <p><strong>Address:</strong> {company['address']}</p>
        <p><strong>Phone:</strong> {company['phone']}</p>
        <p><strong>Email:</strong> {company['email']}</p>
    </div>
</body>
</html>"""

    def generate_robots_txt(self, flag: str, website_name: str) -> Tuple[str, str]:
        """Challenge Type 2: Flag in robots.txt pointing to hidden page"""
        secret_page = f"/secret_{self.fake.uuid4()[:8]}.html"

        robots_content = f"""User-agent: *
Disallow: /admin/
Disallow: /backup/
Disallow: {secret_page}
# Note to developers: The flag is hidden in the disallowed page
"""

        secret_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Secret Area - {website_name}</title>
    <style>
        body {{ font-family: monospace; background: #000; color: #0f0; padding: 40px; }}
        .flag {{ background: #1a1a1a; padding: 20px; border: 2px solid #0f0; margin-top: 20px; }}
    </style>
</head>
<body>
    <h1> RESTRICTED AREA </h1>
    <p>You shouldn't be here... but since you found this page:</p>
    <div class="flag">
        <h2>FLAG:</h2>
        <p style="font-size: 1.5em; color: #ff0;">{flag}</p>
    </div>
    <p>Congratulations on reading robots.txt!</p>
</body>
</html>"""

        return robots_content, secret_html, secret_page

    def generate_source_code_challenge(self, flag: str) -> str:
        """Challenge Type 3: Flag in JavaScript source code"""
        company = self.generate_fake_company_data()

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{company['company']} - Login</title>
    <style>
        body {{ font-family: Arial, sans-serif; display: flex; justify-content: center; align-items: center; height: 100vh; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); margin: 0; }}
        .login-box {{ background: white; padding: 40px; border-radius: 10px; box-shadow: 0 10px 40px rgba(0,0,0,0.2); width: 300px; }}
        input {{ width: 100%; padding: 10px; margin: 10px 0; border: 1px solid #ddd; border-radius: 5px; box-sizing: border-box; }}
        button {{ width: 100%; padding: 12px; background: #667eea; color: white; border: none; border-radius: 5px; cursor: pointer; font-size: 16px; }}
        button:hover {{ background: #5568d3; }}
        .error {{ color: red; font-size: 0.9em; display: none; }}
    </style>
</head>
<body>
    <div class="login-box">
        <h2>Admin Login</h2>
        <form id="loginForm">
            <input type="text" id="username" placeholder="Username" required>
            <input type="password" id="password" placeholder="Password" required>
            <div class="error" id="error">Invalid credentials!</div>
            <button type="submit">Login</button>
        </form>
    </div>

    <script>
        // Debug mode - flag for developers
        const DEBUG_FLAG = "{flag}";
        const ADMIN_HASH = "5e884898da28047151d0e56f8dc6292773603d0d6aabbdd62a11ef721d1542d8"; // password

        document.getElementById('loginForm').addEventListener('submit', function(e) {{
            e.preventDefault();
            const username = document.getElementById('username').value;
            const password = document.getElementById('password').value;

            // In production, this would be a proper API call
            if (username === 'admin' && btoa(password) === btoa('demo123')) {{
                alert('Login successful! But this is just a demo...');
            }} else {{
                document.getElementById('error').style.display = 'block';
            }}
        }});

        // TODO: Remove debug flag before deployment
        console.log('Debug mode active. Flag:', DEBUG_FLAG);
    </script>
</body>
</html>"""

    def generate_base64_challenge(self, flag: str) -> str:
        """Challenge Type 4: Flag encoded in base64"""
        company = self.generate_fake_company_data()
        encoded_flag = base64.b64encode(flag.encode()).decode()

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{company['company']} - API Documentation</title>
    <style>
        body {{ font-family: 'Courier New', monospace; padding: 40px; background: #1e1e1e; color: #d4d4d4; }}
        .container {{ max-width: 1000px; margin: 0 auto; }}
        .endpoint {{ background: #252526; padding: 20px; margin: 20px 0; border-left: 4px solid #007acc; }}
        .method {{ background: #16825d; color: white; padding: 5px 10px; border-radius: 3px; display: inline-block; }}
        code {{ background: #1e1e1e; padding: 2px 6px; border-radius: 3px; color: #ce9178; }}
        pre {{ background: #1e1e1e; padding: 15px; border-radius: 5px; overflow-x: auto; }}
    </style>
</head>
<body>
    <div class="container">
        <h1> {company['company']} API v2.0</h1>
        <p>RESTful API Documentation</p>

        <div class="endpoint">
            <h3><span class="method">GET</span> /api/users</h3>
            <p>Retrieve list of users</p>
            <pre><code>{{
  "users": [
    {{"id": 1, "name": "{self.fake.name()}"}},
    {{"id": 2, "name": "{self.fake.name()}"}}
  ]
}}</code></pre>
        </div>

        <div class="endpoint">
            <h3><span class="method">POST</span> /api/auth</h3>
            <p>Authentication endpoint</p>
            <p><strong>Headers:</strong></p>
            <pre><code>Authorization: Bearer YOUR_TOKEN
X-API-Key: {self.fake.uuid4()}
X-Debug-Token: {encoded_flag}</code></pre>
            <p style="color: #858585; font-size: 0.9em;">Note: X-Debug-Token is base64 encoded for security</p>
        </div>

        <div class="endpoint">
            <h3><span class="method">GET</span> /api/status</h3>
            <p>Check API status</p>
            <pre><code>{{
  "status": "operational",
  "version": "2.0.1",
  "uptime": {random.randint(1000, 99999)}
}}</code></pre>
        </div>
    </div>
</body>
</html>"""

    def generate_hidden_form_challenge(self, flag: str) -> str:
        """Challenge Type 5: Flag in hidden form field"""
        company = self.generate_fake_company_data()

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{company['company']} - Contact Us</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 0; padding: 0; background: #f5f5f5; }}
        .header {{ background: #2c3e50; color: white; padding: 30px; text-align: center; }}
        .container {{ max-width: 600px; margin: 40px auto; background: white; padding: 40px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
        .form-group {{ margin-bottom: 20px; }}
        label {{ display: block; margin-bottom: 5px; font-weight: bold; }}
        input, textarea {{ width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 4px; box-sizing: border-box; }}
        button {{ background: #2c3e50; color: white; padding: 12px 30px; border: none; border-radius: 4px; cursor: pointer; font-size: 16px; }}
        button:hover {{ background: #34495e; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>{company['company']}</h1>
        <p>Get in touch with us</p>
    </div>

    <div class="container">
        <h2>Contact Form</h2>
        <form action="/submit" method="POST">
            <div class="form-group">
                <label for="name">Your Name:</label>
                <input type="text" id="name" name="name" required>
            </div>

            <div class="form-group">
                <label for="email">Email Address:</label>
                <input type="email" id="email" name="email" required>
            </div>

            <div class="form-group">
                <label for="message">Message:</label>
                <textarea id="message" name="message" rows="5" required></textarea>
            </div>

            <!-- Hidden fields for tracking -->
            <input type="hidden" name="form_id" value="contact_v2">
            <input type="hidden" name="timestamp" value="{self.fake.unix_time()}">
            <input type="hidden" name="debug_token" value="{flag}">
            <input type="hidden" name="session_id" value="{self.fake.uuid4()}">

            <button type="submit">Send Message</button>
        </form>
    </div>
</body>
</html>"""

    def generate_website(self, challenge_type: str = None) -> Dict:
        """Generate a complete CTF website challenge"""
        if challenge_type is None:
            challenge_type = random.choice(self.challenge_types[:5])  # Use main 5 types

        flag = self.generate_flag()
        website_id = self.fake.uuid4()[:8]
        website_dir = self.output_dir / f"website_{website_id}"
        website_dir.mkdir(exist_ok=True)

        # Generate main index.html based on challenge type
        if challenge_type == "hidden_comment":
            index_html = self.generate_hidden_comment(flag)
            hint = "The flag is hidden in an HTML comment. Use curl and grep, or view the page source."
            solution = "curl http://localhost/index.html | grep -o 'flag{[^}]*}'"
            partial_credit_markers = {
                "viewed_source": "curl http://localhost",
            }

        elif challenge_type == "robots_txt":
            company = self.generate_fake_company_data()
            robots_content, secret_html, secret_page = self.generate_robots_txt(flag, company['company'])

            # Write robots.txt
            (website_dir / "robots.txt").write_text(robots_content)

            # Write secret page
            (website_dir / secret_page.lstrip('/')).write_text(secret_html)

            # Simple index
            index_html = f"""<!DOCTYPE html>
<html><head><title>{company['company']}</title></head>
<body><h1>Welcome to {company['company']}</h1>
<p>{company['tagline']}</p></body></html>"""

            hint = "Check robots.txt for disallowed paths. The flag is on a hidden page."
            solution = f"curl http://localhost/robots.txt && curl http://localhost{secret_page}"
            partial_credit_markers = {
                "found_robots": "robots.txt",
                "found_secret_page": secret_page.lstrip("/"),
            }

        elif challenge_type == "source_code":
            index_html = self.generate_source_code_challenge(flag)
            hint = "The flag is in the JavaScript source code. Inspect the page source or use curl."
            solution = "curl http://localhost/index.html | grep -o 'flag{[^}]*}'"
            partial_credit_markers = {
                "viewed_source": "curl http://localhost",
                "found_debug_var": "DEBUG_FLAG",
            }

        elif challenge_type == "base64_encoded":
            index_html = self.generate_base64_challenge(flag)
            hint = "The flag is base64 encoded in the API documentation. Look for encoded headers."
            solution = "curl http://localhost/index.html | grep 'X-Debug-Token' | grep -oP '(?<=X-Debug-Token: )[^<]+' | base64 -d"
            partial_credit_markers = {
                "found_encoded_token": "X-Debug-Token",
                "decoded_base64": "base64",
            }

        elif challenge_type == "hidden_form_field":
            index_html = self.generate_hidden_form_challenge(flag)
            hint = "The flag is in a hidden form field. View the HTML source."
            solution = "curl http://localhost/index.html | grep -o 'flag{[^}]*}'"
            partial_credit_markers = {
                "viewed_source": "curl http://localhost",
                "found_hidden_field": "debug_token",
            }

        else:
            index_html = self.generate_hidden_comment(flag)
            hint = "Generic challenge - inspect the HTML source"
            solution = "curl http://localhost/index.html | grep -o 'flag{[^}]*}'"
            partial_credit_markers = {
                "viewed_source": "curl http://localhost",
            }

        # Write index.html
        (website_dir / "index.html").write_text(index_html)

        # Create challenge metadata
        metadata = {
            "challenge_id": website_id,
            "challenge_type": challenge_type,
            "flag": flag,
            "hint": hint,
            "solution": solution,
            "difficulty": random.choice(["easy", "medium"]),
            "partial_credit_markers": partial_credit_markers,
            "website_path": str(website_dir),
            "files": [str(f.name) for f in website_dir.iterdir()]
        }

        # Write metadata
        (website_dir / "challenge.json").write_text(json.dumps(metadata, indent=2))

        return metadata

    def generate_batch(self, count: int = 5) -> List[Dict]:
        """Generate multiple CTF websites"""
        challenges = []
        for i in range(count):
            print(f"Generating challenge {i+1}/{count}...")
            challenge = self.generate_website()
            challenges.append(challenge)

        # Write summary
        summary_file = self.output_dir / "challenges_summary.json"
        summary_file.write_text(json.dumps(challenges, indent=2))

        print(f"\n Generated {count} CTF challenges in {self.output_dir}")
        print(f" Summary saved to {summary_file}")

        return challenges
