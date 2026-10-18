"""Photon replies arrive through the live server, not a second process.

Photon 2.0 (`https://api.photon.codes/v1`, Bearer project keys) is what
`PHOTON_API_KEY` and `pho_prj_` ids talk to. A Spectrum Node sidecar is a
different product, so outbound texts stay in `fluffy.notify.photon`.
"""
