# AUR package

`python-extsearch` — the library plus its console scripts (`extsearch`,
`extsearch-mcp`, `extsearch-api`). Optional features are `optdepends`, so a
minimal install stays small.

## Publish to AUR (first time)

```bash
# 1. account + SSH key
#    https://aur.archlinux.org/register  → paste ~/.ssh/id_ed25519.pub

git clone ssh://aur@aur.archlinux.org/python-extsearch.git
cp PKGBUILD .SRCINFO python-extsearch/
cd python-extsearch
git add PKGBUILD .SRCINFO
git commit -m "Initial import: python-extsearch 2.1.0"
git push
```

## Update for a new release

```bash
# bump pkgver, then:
updpkgsums                       # or recompute sha256sums by hand
makepkg --printsrcinfo > .SRCINFO
git commit -am "upgpkg: python-extsearch 2.1.1"
git push
```

## Check locally before pushing

```bash
yay -S --asdeps python-curl-cffi   # not in the official repos
makepkg -si
namcap PKGBUILD PKGBUILD.proto 2>/dev/null | head
```

## Own repository (`sudo pacman -S extsearch` without AUR)

Only a self-hosted binary repo makes `pacman -S` work for a personal package:

```bash
makepkg -s --sign                                   # produces *.pkg.tar.zst + .sig
repo-add -s -k <GPG_KEY_ID> extsearch.db.tar.gz *.pkg.tar.zst
# upload extsearch.db* , extsearch.files* and the package to a web dir
```

`/etc/pacman.conf` on the user's machine:

```ini
[extsearch]
SigLevel = Required DatabaseOptional
Server = https://your.domain/arch/$arch
```
