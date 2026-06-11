import sys, os, json, hashlib, subprocess, tempfile, shutil, tarfile
import urllib.request, urllib.error

REGISTRY = sys.argv[1]
REPO = sys.argv[2]
TAG = sys.argv[3]

def req(url, headers=None, timeout=60):
    r = urllib.request.Request(url, headers=headers or {})
    return urllib.request.urlopen(r, timeout=timeout)

def download(path, url, headers, desc):
    if os.path.exists(path):
        print(f"  {desc} (cached)", flush=True)
        return
    print(f"  {desc}...", end=" ", flush=True)
    r = urllib.request.Request(url, headers=headers)
    total = 0
    with urllib.request.urlopen(r, timeout=300) as resp:
        with open(path, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                total += len(chunk)
    print(f"{total//1024//1024}MB", flush=True)

# Get token
realm_url = f"https://{REGISTRY}/v2/"
try:
    req(realm_url)
except urllib.error.HTTPError as e:
    import re
    auth = e.headers.get("Www-Authenticate", "")
    realm = re.search(r'realm="([^"]+)"', auth).group(1)
    service = re.search(r'service="([^"]+)"', auth).group(1)

token_url = f"{realm}?service={service}&scope=repository:{REPO}:pull"
print(f"Token...", end=" ", flush=True)
with req(token_url) as r:
    token = json.loads(r.read())["token"]
print("OK", flush=True)

accept = "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json"
auth_headers = {"Authorization": f"Bearer {token}", "Accept": accept}

# Get manifest
print(f"Manifest {TAG}...", end=" ", flush=True)
with req(f"https://{REGISTRY}/v2/{REPO}/manifests/{TAG}", auth_headers) as r:
    manifest = json.loads(r.read())
print(f"type={manifest.get('mediaType','?')[:40]}", flush=True)

# Handle manifest list
if "manifests" in manifest:
    selected = None
    for m in manifest["manifests"]:
        p = m.get("platform", {})
        if p.get("os") == "linux" and p.get("architecture") == "amd64":
            selected = m
            break
    if not selected:
        selected = manifest["manifests"][0]
    print(f"  Picked {selected.get('platform',{})}", flush=True)
    with req(f"https://{REGISTRY}/v2/{REPO}/manifests/{selected['digest']}", auth_headers) as r:
        manifest = json.loads(r.read())

layers = manifest.get("layers", [])
config = manifest.get("config", {})
print(f"Layers: {len(layers)}", flush=True)

# Create temp dir
tmpdir = tempfile.mkdtemp(prefix="img_")
blobs = os.path.join(tmpdir, "blobs", "sha256")
os.makedirs(blobs, exist_ok=True)

# Download config
if config.get("digest"):
    h = config["digest"].split(":")[1]
    download(os.path.join(blobs, h), f"https://{REGISTRY}/v2/{REPO}/blobs/{config['digest']}", auth_headers, f"Config {h[:12]}")

# Download layers
for i, layer in enumerate(layers):
    h = layer["digest"].split(":")[1]
    download(os.path.join(blobs, h), f"https://{REGISTRY}/v2/{REPO}/blobs/{layer['digest']}", auth_headers, f"Layer {i+1}/{len(layers)} {h[:12]}")

# Write manifest
mraw = json.dumps(manifest).encode()
mhash = hashlib.sha256(mraw).hexdigest()
with open(os.path.join(blobs, mhash), "wb") as f:
    f.write(mraw)

# OCI layout
with open(os.path.join(tmpdir, "oci-layout"), "w") as f:
    json.dump({"imageLayoutVersion": "1.0.0"}, f)

# index.json
with open(os.path.join(tmpdir, "index.json"), "w") as f:
    json.dump({
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": [{
            "mediaType": manifest.get("mediaType", "application/vnd.oci.image.manifest.v1+json"),
            "digest": f"sha256:{mhash}",
            "size": len(mraw),
            "annotations": {"org.opencontainers.image.ref.name": TAG}
        }]
    }, f, indent=2)

# Create tar
tar_path = os.path.join(tempfile.gettempdir(), f"{REPO.replace('/','_')}_{TAG}.tar")
print(f"Creating tar...", end=" ", flush=True)
with tarfile.open(tar_path, "w") as tar:
    tar.add(tmpdir, arcname=".")
print(f"OK ({os.path.getsize(tar_path)//1024//1024}MB)", flush=True)

# Load into Docker
print("Loading into Docker...", end=" ", flush=True)
r = subprocess.run(["docker", "load", "-i", tar_path], capture_output=True, text=True, timeout=120)
if r.returncode != 0:
    print(f"ERROR: {r.stderr}")
    sys.exit(1)
print(r.stdout.strip(), flush=True)

# Cleanup
shutil.rmtree(tmpdir)
os.remove(tar_path)
print("DONE!", flush=True)
