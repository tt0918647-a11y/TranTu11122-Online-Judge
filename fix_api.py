
from pathlib import Path

p = Path("templates/index.html")

if not p.exists():
    raise SystemExit(
        "Khong tim thay templates/index.html. "
        "Hay chay script tai thu muc goc cua repository."
    )

s = p.read_text(encoding="utf-8")
start = s.find("async function api(")

if start == -1:
    raise SystemExit(
        "Khong tim thay ham api. "
        "File chua duoc sua; khong co gi bi thay doi."
    )

# Tim dau { cua than ham
brace = s.find("{", start)
if brace == -1:
    raise SystemExit("Khong tim thay than ham api.")

# Tim dau } ket thuc ham, bo qua ky tu trong chuoi va comment
depth = 0
quote = None
escape = False
line_comment = False
block_comment = False
end = -1
i = brace

while i < len(s):
    c = s[i]
    n = s[i + 1] if i + 1 < len(s) else ""

    if line_comment:
        if c == "\n":
            line_comment = False
    elif block_comment:
        if c == "*" and n == "/":
            block_comment = False
            i += 1
    elif quote:
        if escape:
            escape = False
        elif c == "\\":
            escape = True
        elif c == quote:
            quote = None
    elif c == "/" and n == "/":
        line_comment = True
        i += 1
    elif c == "/" and n == "*":
        block_comment = True
        i += 1
    elif c in ("'", '"', "`"):
        quote = c
    elif c == "{":
        depth += 1
    elif c == "}":
        depth -= 1
        if depth == 0:
            end = i + 1
            break

    i += 1

if end == -1:
    raise SystemExit(
        "Khong tim duoc cuoi ham api. File chua duoc sua."
    )

new_function = r'''async function api(url, opts = {}) {
    let r;

    try {
        r = await fetch(url, {
            ...opts,
            headers: {
                'Content-Type': 'application/json',
                ...(opts.headers || {})
            }
        });
    } catch (e) {
        throw Error('Không kết nối được máy chủ: ' + e.message);
    }

    const raw = await r.text();
    let d;

    try {
        d = JSON.parse(raw);
    } catch (e) {
        throw Error(
            'Phản hồi không phải JSON. HTTP ' +
            r.status + ': ' +
            (raw.slice(0, 500) || '(phản hồi rỗng)')
        );
    }

    if (!r.ok) {
        const detail = d.error || d.message || JSON.stringify(d);
        throw Error('HTTP ' + r.status + ': ' + detail);
    }

    return d;
}'''

backup = p.with_suffix(".html.bak")
backup.write_text(s, encoding="utf-8")

updated = s[:start] + new_function + s[end:]
p.write_text(updated, encoding="utf-8")

print("Da sua templates/index.html")
print("Ban sao cu:", backup)
print("Hay kiem tra thay doi truoc khi day len GitHub.")
