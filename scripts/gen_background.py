"""Generate a scenery-only background plate (no characters) for puppet scenes."""
import sys, json, time, urllib.request, urllib.parse, os
sys.path.insert(0, r"C:\pradeep\animator\web_ui")
import pipeline_api as P

COMFY = "http://localhost:8188"
OUT = r"C:\pradeep\animator\outputs\bg_plates"
os.makedirs(OUT, exist_ok=True)
ckpt = P._find_checkpoint()

setting = sys.argv[1] if len(sys.argv) > 1 else "sunny park meadow with a pond"
name = sys.argv[2] if len(sys.argv) > 2 else "park_pond"
PROMPT = ("cartoon, flat color, children's illustration, 2d, bold outlines, bright, "
          f"{setting}, green grass, trees, blue sky with clouds, flowers, "
          "empty scenery, wide establishing shot, no animals, no characters, no people")
NEG = ("animals, ducks, rabbits, cats, characters, people, person, creatures, foreground subject, "
       "text, watermark, realistic, photo, dark")

wf = P._comfyui_workflow(PROMPT, NEG, f"bg_{name}", ckpt)
for node in wf.values():
    if node.get("class_type") == "KSampler":
        node["inputs"]["seed"] = 555
req = urllib.request.Request(f"{COMFY}/prompt", data=json.dumps({"prompt": wf}).encode(),
                             headers={"Content-Type": "application/json"})
pid = json.loads(urllib.request.urlopen(req, timeout=30).read())["prompt_id"]
for _ in range(120):
    time.sleep(3)
    h = json.loads(urllib.request.urlopen(f"{COMFY}/history/{pid}", timeout=10).read())
    if pid in h:
        for node in h[pid]["outputs"].values():
            for img in node.get("images", []):
                url = f"{COMFY}/view?" + urllib.parse.urlencode(
                    {"filename": img["filename"], "subfolder": img.get("subfolder", ""), "type": img["type"]})
                raw = urllib.request.urlopen(url, timeout=30).read()
                p = os.path.join(OUT, f"{name}.png")
                open(p, "wb").write(raw)
                print("saved", p)
        break
