import sys
import re
import os
import shutil
import stat
import subprocess
import logging
import json
import tempfile
import traceback
import dast_plugins.skipfish as skipfish

dast_plugins = ["skipfish"]

def get_inventory(args):
    if args.plugin not in dast_plugins:
        logging.error('Not a supported DAST plugin')
        sys.exit(1) 

    asset_id = args.assetid
    if asset_id == None:
        asset_id = args.url
    asset_name = None
    if args.assetname == None:
        asset_name = asset_id 
    else:
        asset_name = args.assetname

    asset_data = {}
    asset_data['id'] = asset_id
    asset_data['name'] = asset_name
    asset_data['type'] = "Web Application" 
    asset_data['owner'] = args.handle
    asset_data['products'] = [] 
    asset_data['tags'] = [args.url] 

    logging.info("Running DAST plugin for web application")

    asset_data['config_issues'] = run_dast(args)

    return [ asset_data ]

def run_dast(args):
    findings = []
    if args.plugin == 'skipfish':
        findings = skipfish.run(args)
    #print json.dumps(findings, indent=4)
    return findings