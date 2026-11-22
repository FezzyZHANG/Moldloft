# ELK browser distribution

Pinned dependency: **elkjs 0.12.0**, unmodified upstream files.

- Source: [kieler/elkjs 0.12.0](https://github.com/kieler/elkjs/tree/0.12.0)
- Package: [elkjs 0.12.0](https://www.npmjs.com/package/elkjs/v/0.12.0)
- Layout engine source: [Eclipse Layout Kernel](https://github.com/eclipse-elk/elk)
- Upstream package license: `EPL-2.0 OR GPL-3.0-or-later`. The complete upstream
  Eclipse Public License 2.0 `LICENSE.md`, including its secondary-license notice,
  accompanies the files.
- `elk-api.js` is the browser API; `elk-worker.min.js` is the local layout worker. No local
  patches have been applied. Upstream license comments are retained.

The repository's `.gitattributes` disables newline conversion and whitespace diagnostics
only for these two unmodified upstream distributions. Their original trailing whitespace
is preserved; the hashes below verify the bytes. Project-authored files keep normal checks.

| File | SHA-256 |
|---|---|
| `elk-api.js` | `ccca46175c05bbd280b5de6c9853103711e52c666a101f145b717e345363e24d` |
| `elk-worker.min.js` | `e4f5c51c6f2fd564bb64e52e81c4a85a316ef2f24f8b90e1db5eb886c765b030` |

Reproduce the distribution in a temporary directory (Node/npm needed only for maintenance):

```text
npm pack elkjs@0.12.0 --ignore-scripts
tar -xf elkjs-0.12.0.tgz
```

Copy `package/lib/elk-api.js`, `package/lib/elk-worker.min.js` and `package/LICENSE.md` here.
Verify the hashes with `Get-FileHash -Algorithm SHA256` on Windows. When updating versions,
update this record/license, run the compound endpoint and 500/1000 layout fixtures, then
check the actual browser worker and offline loading. Do not regenerate the worker yourself
or remove its notices. Source and distribution availability are documented above for recipients.

These distributions are intentionally versioned for reproducible offline startup. This is
the narrow vendoring exception inherited from DEC-2026-008 and recorded in the repository-local extraction decision, not a policy of tracking
regenerable application outputs. Their source/provenance and license remain reviewable.
