# Linux setup maintenance

The Linux setup workflow runs on PRs, manually, and weekly after merge. Scheduled
failures open one maintenance issue linked to the run; investigate package names,
runtime compatibility, and logs before closing it. CI uses the same setup entry
point/package manifest as the documented install path. Dependabot tracks declared
runtime dependencies and CI actions; distro libraries remain managed by the host
package manager. Neither mechanism merges updates or upgrades users' machines.

Keep setup appropriate to this repository's runtime and deployment model. Test
without personal credentials or physical-device changes. Report which distro and
runtime checks passed; container tests do not verify host services, GPU drivers,
USB access, or real hardware. Keep existing data, configuration, and environment
files on reruns; never hide a dependency failure as successful installation.
