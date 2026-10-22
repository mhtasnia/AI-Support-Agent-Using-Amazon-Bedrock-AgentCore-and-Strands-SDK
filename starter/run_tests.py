import json, shutil, subprocess, time, os

os.environ["AGENTCORE_SUPPRESS_RECOMMENDATION"] = "1"
AGENTCORE = shutil.which("agentcore") or "agentcore"

def invoke(prompt, session):
    payload = json.dumps({"prompt": prompt, "customer_id": "CUST-123", "session_id": session})
    print(f"\n=== [{session}] {prompt} ===")
    result = subprocess.run([AGENTCORE, "invoke", payload], capture_output=True, text=True)
    print(result.stdout or result.stderr)

invoke("Can you track order ORD-001?", "t1")
invoke("I want to return my Kindle Paperwhite (ORD-002). Please initiate a refund.", "t2")
invoke("What are the benefits of the Platinum loyalty tier?", "t3")

invoke("Hi, I am Jane. I prefer concise responses.", "s-A")
print("\nWaiting 45 seconds for memory extraction...")
time.sleep(45)
invoke("Do you remember my name and communication preference?", "s-B")

invoke("I am a Gold member with 4250 points. Calculate my discount on a $150 standard order.", "t5")
invoke("Go to https://www.udacity.com and tell me the page title.", "t6")