#!/usr/bin/ucode -S
// Copyright (c) 2025-2026 Eric Fahlgren <eric.fahlgren@gmail.com>
// SPDX-License-Identifier: GPL-2.0-only
// vim: set noexpandtab softtabstop=8 shiftwidth=8 syntax=javascript:
//------------------------------------------------------------------------------
//
// An apk utility to scan the installed packages and create a new world
// file containing only two things:
//
//   1) The top-level packages, upon which no other packages depend;
//   2) Pinned packages, i.e., those that are specified in the world
//      with a version constraint.
//
// If the output contains more entries than the current world file, you can
// copy the output there safely.  A typical command sequence to update your
// world might look like this, using vimdiff to cherry pick changes:
//
//   $ world-cleaner.uc > new_world
//   $ vimdiff new_world /etc/apk/world
//
// Note that you should not just redirect the output from this to overwrite
// world directly, as that will delete information in world before it can
// be read.
//
//   $ world-cleaner.uc > /etc/apk/world  # BAD: DON'T DO THIS


import * as fs from "fs";


let verbose = "--verbose" in ARGV;  // Super crappy.

function log(fmt, ...args)
{
	if (verbose) {
		warn(sprintf(fmt, ...args));
	}
}


let packages = {
//	pkg: {
//		version: str,     // Unused, possibly useful for diagnostics.
//		abiversion: str,  // Used to clean 'pkg' and depends entries.
//		depends: [ ... ], // The dependency info.
//              constraint: str,  // Usually 'null', any constraint from world.
//	},
//	...
};

function abi_clean(pkg_name)
{
	let name = pkg_name;
	if (pkg_name in packages) {
		let abiv = packages[pkg_name].abiversion;
		if (abiv) {
			name = substr(name, 0, -length(abiv));
		}
	}
	return name;
}

function abi_versioned(pkg_name)
{
	// Do the much more intensive conversion from non-versioned to
	// versioned.

	if (pkg_name in packages) {
		// Shortcut: Assume it's already in versioned form.
		return pkg_name;
	}

	for (let pkg, info in packages) {
		if (pkg_name == abi_clean(pkg)) {
			return pkg;
		}
	}

	return pkg_name;
}

function is_top_level(pkg_name)
{
	let name = abi_clean(pkg_name);
	for (let pkg, info in packages) {
		for (let dep in info.depends) {
			if (name == abi_clean(dep)) return false;
		}
	}
	return true;
}

//-- Parse the installed packages ---------------------------------------------

let line;

let pkg = null;
let version = null;
let depends = null;
let abiversion = null;
let constraint = "";

// https://wiki.alpinelinux.org/wiki/Apk_spec
let db = fs.open("/lib/apk/db/installed", "r");
while (line = db.read("line")) {
	line = trim(line);
	if (! line) {
		//printf("add %s %s %s\n", pkg, version);
		packages[pkg] = { version, depends, abiversion, constraint };
		pkg = version = depends = abiversion = null;
	}
	else {
		let prefix = substr(line, 0, 2);
		let tail   = substr(line, 2);
		switch (prefix) {
			case "P:":
				pkg = tail;
				break;
			case "V:":
				version = tail;
				break;
			case "D:":
				depends = map(split(tail, " "), (s) => split(s, /[<=>]+/)[0]);
				break;
			case "g:":
				for (let tag in split(tail, " ")) {
					tag = match(tag, /^openwrt:abiversion=(.*)/);
					if (tag) {
						abiversion = tag[1];
						break;
					}
				}
				break;
		}
	}
}
db.close();
if (pkg != null) warn(`ERROR: db/installed corrupted at ${pkg}\n`);

//-- Collect any pinned version constraints -----------------------------------
//   We expect world file to have same ABI-versioned name as in 'packages',
//   so coerce ones that are ABI-clean to their versioned form.

let splitter = regexp("([^@~<>=]*)(.*)"); // separators from man 5 apk-world

let world_file = fs.open("/etc/apk/world", "r");
let world_list = [];
while (line = world_file.read("line")) {
	line = trim(line);
	let parts = match(line, splitter);
	pkg = parts[1];
	constraint = parts[2];

	let versioned = abi_versioned(pkg);
	if (pkg != versioned) {
		log("INFO: '%s' is not ABI-versioned, renamed '%s'...\n", pkg, versioned);
		pkg = versioned;
	}

	if (! (pkg in packages)) {
		warn(`WARNING: ${pkg} in world, but not in db/installed\n`);
		continue;
	}

	packages[pkg].constraint = constraint;
	push(world_list, pkg);
}
world_file.close();

//-- Compute top-level world --------------------------------------------------

let world = {};
for (let pkg, info in packages) {
	if (is_top_level(pkg) || info.constraint) {
		// If a package version has been pinned/constrained, it must be in world.
		world[pkg] = { version: info.version, constraint: info.constraint };
	}
	else if (pkg in world_list) {
		warn(`Remove: ${pkg}\n`);
	}
}

//-- Write result in world format ---------------------------------------------

for (let pkg, info in world) {
	printf("%s%s\n", pkg, info.constraint);
}
