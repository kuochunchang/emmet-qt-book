# Vendored MathJax

- Package: `mathjax`
- Version: `4.0.0`
- Source tarball: `https://registry.npmjs.org/mathjax/-/mathjax-4.0.0.tgz`
- Tarball SHA-256:
  `56fc233d745e887349a8af9cb3c6cfc2280e169a98c3853578b83049d16b272a`
- `tex-svg.js` SHA-256:
  `8fcdaf8760790105412b6b5a087cf189f84abc8ed81731190363352dfcbbb44b`
- License: Apache-2.0; see `LICENSE` in this directory.

The HTML book loads this fixed, local TeX-to-SVG combined component. It does not
load MathJax from a CDN. When updating it, verify both checksums, rebuild the book,
and run the browser rendering check.
