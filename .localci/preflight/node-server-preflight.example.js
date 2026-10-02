#!/usr/bin/env node
/*
 * Dependency-free Node.js server preflight template.
 * Copy this file and its JSON config into the product repository, then run:
 *   node .localci/preflight/node-server-preflight.js \
 *     --config .localci/preflight/node-server-preflight.json
 */
"use strict";

const fs = require("node:fs");
const http = require("node:http");
const https = require("node:https");
const path = require("node:path");

function argument(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

function readConfig() {
  const filename = argument("--config") || process.env.LOCALCI_NODE_PREFLIGHT_CONFIG;
  if (!filename) throw new Error("--config is required");
  const resolved = path.resolve(filename);
  return {resolved, config: JSON.parse(fs.readFileSync(resolved, "utf8"))};
}

function valueAt(object, expression) {
  return expression.split(".").reduce((value, key) => (
    value === null || value === undefined ? undefined : value[key]
  ), object);
}

function checkPackages(config, errors) {
  for (const name of config.packages || []) {
    try {
      require.resolve(name, {paths: [process.cwd()]});
    } catch (error) {
      errors.push(`package=${name}: ${error.code || "not resolvable"}`);
    }
  }
}

function checkEnvironment(config, errors) {
  for (const name of config.env || []) {
    if (!process.env[name]) errors.push(`env=${name}: missing`);
  }
}

function requestJson(target, timeoutMs) {
  return new Promise((resolve, reject) => {
    const url = new URL(target.url);
    const client = url.protocol === "https:" ? https : http;
    const request = client.request(url, {
      method: target.method || "GET",
      headers: target.headers || {},
      timeout: timeoutMs,
    }, (response) => {
      let body = "";
      response.setEncoding("utf8");
      response.on("data", (chunk) => { body += chunk; });
      response.on("end", () => {
        let json;
        try { json = JSON.parse(body); } catch (_) { json = undefined; }
        resolve({status: response.statusCode || 0, json});
      });
    });
    request.on("timeout", () => request.destroy(new Error("timeout")));
    request.on("error", reject);
    request.end();
  });
}

async function checkHttp(config, errors) {
  for (const target of config.http || []) {
    if (!target.url) {
      errors.push("http: url is required");
      continue;
    }
    try {
      const response = await requestJson(target.url, target.timeout_ms || 5000);
      const expected = target.expect_status || 200;
      if (response.status !== expected) {
        errors.push(`http=${target.url}: status=${response.status}, expected=${expected}`);
        continue;
      }
      for (const assertion of target.json_paths || []) {
        const value = valueAt(response.json, assertion.path);
        if (assertion.not_null !== false && (value === null || value === undefined)) {
          errors.push(`http=${target.url}: ${assertion.path} is null or missing`);
          continue;
        }
        if (assertion.type && value !== null && value !== undefined && typeof value !== assertion.type) {
          errors.push(`http=${target.url}: ${assertion.path} type=${typeof value}, expected=${assertion.type}`);
        }
      }
    } catch (error) {
      errors.push(`http=${target.url}: ${error.message}`);
    }
  }
}

async function main() {
  const errors = [];
  let loaded;
  try {
    loaded = readConfig();
  } catch (error) {
    console.error(`preflight configuration failed: ${error.message}`);
    return 2;
  }
  checkPackages(loaded.config, errors);
  checkEnvironment(loaded.config, errors);
  await checkHttp(loaded.config, errors);
  if (errors.length) {
    console.error(`node server preflight failed: ${loaded.resolved}`);
    for (const error of errors) console.error(`  - ${error}`);
    return 1;
  }
  console.log(`node server preflight passed: ${loaded.resolved}`);
  return 0;
}

main().then((code) => process.exit(code));
