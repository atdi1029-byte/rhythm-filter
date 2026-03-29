"""
Initialize CoinStats sheet with approved coins, sorted by market cap.
Sends in chunks (init_coins clears + writes first chunk,
append_coins adds remaining chunks).
Requires Apps Script redeployment with append_coins action.
"""
import json
import urllib.request
import urllib.parse
import ssl
import time

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

APPS_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbyVRrk_TFiTZpbqy9Q36muKZ3grqjrl28dfgT5xrOP9yEBa-BUSlOE8ezsDegkHvTUz/exec"

with open("data/coin_results_7_12.json") as f:
    results = json.load(f)
with open("data/approved_coins_7_12.json") as f:
    approved = set(json.load(f))

coins = [c for c in results if c["coin"] in approved]
print(f"Approved coins: {len(coins)}")

MCAP_RANK = {
    "btc":1,"eth":2,"xrp":3,"bnb":4,"sol":5,
    "doge":6,"ada":7,"avax":8,"link":9,"shib":10,
    "dot":11,"sui":12,"near":13,"icp":14,"apt":15,
    "uni":16,"etc":17,"render":18,"hbar":19,"atom":20,
    "fil":21,"imx":22,"op":23,"inj":24,"ftm":25,
    "theta":26,"algo":27,"grt":28,"xlm":29,"ondo":30,
    "aave":31,"floki":32,"bonk":33,"wif":34,"pepe":35,
    "mkr":36,"ldo":37,"axs":38,"snx":39,"comp":40,
    "chz":41,"sand":42,"mana":43,"gala":44,"enj":45,
    "lrc":46,"sushi":47,"zec":48,"neo":49,"xtz":50,
    "egld":51,"iota":52,"flow":53,"zil":54,"1inch":55,
    "crv":56,"kava":57,"ksm":58,"qnt":59,"ape":60,
    "skl":61,"wld":62,"ena":63,"trump":64,"axl":65,
    "bch":66,"dash":67,"lpt":68,"yfi":69,"celo":70,
    "qtum":71,"matic":72,"zrx":73,"fet":74,"ilv":75,
    "storj":76,"dgb":77,"blur":78,"knc":79,"band":80,
    "rvn":81,"ocean":82,"trac":83,"icx":84,"ctsi":85,
    "vet":86,"virtual":87,"pengu":88,"anime":89,
    "ach":90,"one":91,"lsk":92,"req":93,"stg":94,
    "s":95,"t":96,"a":97,"bnt":98,
    "orbs":99,"pond":100,"vtho":101,"stmx":102,
    "forth":103,"nmr":104,"santos":105,"lazio":106,
    "porto":107,"ren":108,"ogn":109,"voxel":110,
    "jam":111,"clv":112,"boson":113,"rare":114,
    "lto":115,"orca":116,"celr":117,"neiro":118,
    "mxc":119,"vite":120,"flux":121,"kda":122,
    "prom":123,"magic":124,"me":125,"turbo":126,
    "astr":127,"loom":128,"sky":129,"xdc":130,
    "dood":131,"giga":132,"jto":133,"a2z":134,
    "1000mog":135,"pump":136,"reef":137,"alice":138,
    "bico":139,"tlm":140,"coti":141,"1000rekt":142,
    "brett":143,"useless":144,"xno":145,
    "gtc":146,"pol":147,
}

for c in coins:
    sym = c["coin"].replace("usdt", "")
    c["rank"] = MCAP_RANK.get(sym, 500)

coins.sort(key=lambda x: x["rank"])

print(f"\n{'#':>3} {'Coin':<16} {'WR%':>6} {'P&L%':>8} {'Kelly':>7} {'Trades':>7}")
print("-" * 55)
for i, c in enumerate(coins):
    sym = c["coin"].replace("usdt", "").upper()
    print(f"{i+1:>3} {sym:<16} "
          f"{c['wr']:>5.1f}% {c['pnl']:>7.0f}% "
          f"{c['kelly']:>6.3f} {c['trades']:>7}")

# Build minimal payload
def make_payload(coin_list):
    p = []
    for c in coin_list:
        p.append({
            "coin": c["coin"],
            "wr": round(c["wr"], 1),
            "pnl": round(c["pnl"], 0),
            "avg_pnl": round(c["avg_pnl"], 2),
            "kelly": round(c["kelly"], 3),
            "max_loss_streak": c["max_loss_streak"],
            "trades": c["trades"]
        })
    return p

def push_chunk(action, data):
    data_str = json.dumps(data, separators=(",", ":"))
    encoded = urllib.parse.quote(data_str)
    url = f"{APPS_SCRIPT_URL}?action={action}&data={encoded}"
    print(f"  URL length: {len(url)} chars, {len(data)} coins")
    req = urllib.request.Request(url)
    resp = urllib.request.urlopen(req, context=ctx, timeout=30)
    result = json.loads(resp.read())
    print(f"  Result: {result}")
    return result

# Split into chunks of 12 (keeps URL under 2K)
CHUNK = 12
chunks = []
for i in range(0, len(coins), CHUNK):
    chunks.append(coins[i:i+CHUNK])

print(f"\nSending {len(chunks)} chunks...")

for i, chunk in enumerate(chunks):
    action = "init_coins" if i == 0 else "append_coins"
    payload = make_payload(chunk)
    print(f"\nChunk {i+1}/{len(chunks)} ({action}):")
    try:
        push_chunk(action, payload)
    except Exception as e:
        print(f"  Failed: {e}")
    time.sleep(2)

# Update file
sorted_coins = [c["coin"] for c in coins]
with open("data/approved_coins_7_12.json", "w") as f:
    json.dump(sorted_coins, f, indent=2)
print("\nUpdated approved_coins_7_12.json (sorted by market cap)")
