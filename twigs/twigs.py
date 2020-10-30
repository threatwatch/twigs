#!/usr/bin/env python
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program.  If not, see <https://www.gnu.org/licenses/>.

import sys
import os
import logging
import argparse
import requests
import time
import json
import traceback
import pkg_resources
import pkgutil
import importlib

from . import aws
from . import linux
from . import repo
from . import docker
from . import azure
from . import gcp
from . import servicenow
from . import inv_file
from . import fingerprint
from . import dast 
from . import docker_cis
from . import aws_cis
from . import azure_cis
from . import gcp_cis
from . import policy as policy_lib
from .__init__ import __version__


GoDaddyCABundle = True

def export_assets_to_file(assets, json_file):
    logging.info("Exporting assets to JSON file [%s]", json_file)
    with open(json_file, "w") as fd:
        json.dump(assets, fd, indent=2, sort_keys=True)
    logging.info("Successfully exported assets to JSON file!")

def push_asset_to_TW(asset, args):
    asset_url = "https://" + args.instance + "/api/v2/assets/"
    auth_data = "?handle=" + args.handle + "&token=" + args.token + "&format=json"
    if args.email_report:
        auth_data = auth_data + "&esr=true" # email secrets report (esr)
    asset_id = asset['id']

    resp = requests.get(asset_url + asset_id + "/" + auth_data, verify=GoDaddyCABundle)
    if resp.status_code != 200:
        logging.info("Creating new asset [%s]", asset_id)
        # Asset does not exist so create one with POST
        resp = requests.post(asset_url + auth_data, json=asset, verify=GoDaddyCABundle)
        if resp.status_code == 200:
            logging.info("Successfully created new asset [%s]", asset_id)
            logging.info("Response content: %s", resp.content.decode(args.encoding))
            return asset_id
        else:
            logging.error("Failed to create new asset [%s]", asset_id)
            logging.error("Response details: %s", resp.content.decode(args.encoding))
            return None
    else:
        logging.info("Updating asset [%s]", asset_id)
        # asset exists so update it with PUT
        resp = requests.put(asset_url + asset_id + "/" + auth_data, json=asset, verify=GoDaddyCABundle)
        if resp.status_code == 200:
            logging.info("Successfully updated asset [%s]", asset_id)
            logging.info("Response content: %s", resp.content.decode(args.encoding))
            return asset_id
        else:
            logging.error("Failed to update existing asset [%s]", asset_id)
            logging.error("Response details: %s", resp.content.decode(args.encoding))
            return None

def push_assets_to_TW(assets, args):
    asset_id_list = []
    for asset in assets:
        asset_id = push_asset_to_TW(asset, args)
        if asset_id is not None:
            asset_id_list.append(asset_id)
    return asset_id_list

def run_scan(asset_id_list, pj_json, args):
    if args.no_scan is not True:
        if len(asset_id_list) == 0:
            logging.info("No assets to scan...")
            return 
        run_va_scan = True
        run_lic_scan = True
        if pj_json is not None:
            for policy in pj_json['policy_json']:
                if policy['type'] == 'vulnerability':
                    logging.info("Impact assessment performed as part of policy evaluation...")
                    run_va_scan = False # VA scan already done, so don't do it again
                elif policy['type'] == 'license':
                    logging.info("License compliance performed as part of policy evaluation...")
                    run_lic_scan = False # License scan already done, so don't do it again

        scan_api_url = "https://" + args.instance + "/api/v1/scans/?handle=" + args.handle + "&token=" + args.token + "&format=json"
        if run_va_scan:
            # Start VA
            logging.info("Starting impact refresh for assets: %s", ",".join(asset_id_list))
            scan_payload = { }
            scan_payload['scan_type'] = 'full' 
            scan_payload['assets'] = asset_id_list
            # if args.purge_assets:
            #    scan_payload['mode'] = 'email-purge'
            if args.email_report:
                scan_payload['mode'] = 'email'
            resp = requests.post(scan_api_url, json=scan_payload, verify=GoDaddyCABundle)
            if resp.status_code == 200:
                logging.info("Started impact refresh...")
            else:
                logging.error("Failed to start impact refresh")
                logging.error("Response details: %s", resp.content.decode(args.encoding))
        if run_lic_scan and (args.mode == "repo" or args.mode == "file_repo"):
            # Start license compliance assessment
            logging.info("Starting license compliance assessment for assets: %s", ",".join(asset_id_list))
            scan_payload = { }
            scan_payload['assets'] = asset_id_list
            scan_payload['license_scan'] = True
            # if args.purge_assets:
            #    scan_payload['mode'] = 'email-purge'
            if args.email_report:
                scan_payload['mode'] = 'email'
            resp = requests.post(scan_api_url, json=scan_payload, verify=GoDaddyCABundle)
            if resp.status_code == 200:
                logging.info("Started license compliance assessment...")
            else:
                logging.error("Failed to start license compliance assessment")
                logging.error("Response details: %s", resp.content.decode(args.encoding))

def add_asset_tags(assets, tags):
    for asset in assets:
        existing_tags = asset.get('tags')
        if existing_tags is None:
            asset['tags'] = tags
        else:
            existing_tags.extend(tags)

def add_asset_criticality_tag(assets, asset_criticality):
    asset_criticality_tag = 'CRITICALITY:'+str(asset_criticality)
    add_asset_tags(assets, [asset_criticality_tag])

def sub_pkg_get_inventory(args):
    dist = "twigs_" + args.mode
    try:
        pkg_resources.get_distribution(dist)
    except pkg_resources.DistributionNotFound:
        logging.error("Error required package [%s] is not installed...", dist)
        logging.error("Please install using command [sudo pip install %s]", dist)
        sys.exit(1)
    module = importlib.import_module('%s.%s' % (dist,dist))
    get_inventory_func = getattr(module, "get_inventory")
    assets = get_inventory_func(args)
    return assets

def main(args=None):

    try:
    
        if args is None:
            args = sys.argv[1:]

        global GoDaddyCABundle
        if sys.platform != 'win32':
            GoDaddyCABundle = os.path.dirname(os.path.realpath(__file__)) + os.sep + 'gd-ca-bundle.crt'
        logfilename = "twigs.log"
        logging_level = logging.WARN

        parser = argparse.ArgumentParser(description='ThreatWatch Information Gathering Script (twigs) to discover assets like hosts, cloud instances, containers and project repositories')
        subparsers = parser.add_subparsers(title="modes", description="Discovery modes supported", dest="mode")
        # Required arguments
        parser.add_argument('-v', '--version', action='version', version='%(prog)s ' + __version__)
        parser.add_argument('--handle', help='The ThreatWatch registered email id/handle of the user. Note this can set as "TW_HANDLE" environment variable', required=False)
        parser.add_argument('--token', help='The ThreatWatch API token of the user. Note this can be set as "TW_TOKEN" environment variable', required=False)
        parser.add_argument('--instance', help='The ThreatWatch instance. Note this can be set as "TW_INSTANCE" environment variable')
        parser.add_argument('--tag_critical', action='store_true', help='Tag the discovered asset(s) as critical')
        parser.add_argument('--tag', action='append', help='Add specified tag to discovered asset(s). You can specify this option multiple times to add multiple tags')
        #parser.add_argument('--asset_criticality', choices=['1', '2', '3','4', '5'], help='Business criticality of the discovered assets on a scale of 1 (low) to 5 (high).', required=False)
        parser.add_argument('--apply_policy', help='One or more policy names as a comma-separated list', required=False)
        parser.add_argument('--out', help='Specify name of the JSON file to hold the exported asset information.')
        parser.add_argument('--no_scan', action='store_true', help='Do not initiate a baseline assessment')
        parser.add_argument('--email_report', action='store_true', help='After impact refresh is complete email scan report to self')
        parser.add_argument('--quiet', action='store_true', help='Disable verbose logging')
        if sys.platform != 'win32':
            parser.add_argument('--schedule', help='Run this twigs command at specified schedule (crontab format)')
        parser.add_argument('--encoding', help='Specify the encoding. Default is "latin-1"', default='latin-1')
        parser.add_argument('--insecure', action='store_true', help=argparse.SUPPRESS)
        # parser.add_argument('--purge_assets', action='store_true', help='Purge the asset(s) after impact refresh is complete and scan report is emailed to self')

        # Arguments required for AWS discovery
        parser_aws = subparsers.add_parser ("aws", help = "Discover AWS instances")
        parser_aws.add_argument('--aws_account', help='AWS account ID', required=True)
        parser_aws.add_argument('--aws_access_key', help='AWS access key', required=True)
        parser_aws.add_argument('--aws_secret_key', help='AWS secret key', required=True)
        parser_aws.add_argument('--aws_region', help='AWS region', required=True)
        parser_aws.add_argument('--aws_s3_bucket', help='AWS S3 inventory bucket', required=True)
        parser_aws.add_argument('--enable_tracking_tags', action='store_true', help='Enable recording AWS specific information (like AWS Account ID, etc.) as asset tags', required=False)

        # Arguments required for Azure discovery
        parser_azure = subparsers.add_parser ("azure", help = "Discover Azure instances")
        parser_azure.add_argument('--azure_tenant_id', help='Azure Tenant ID', required=True)
        parser_azure.add_argument('--azure_application_id', help='Azure Application ID', required=True)
        parser_azure.add_argument('--azure_application_key', help='Azure Application Key', required=True)
        parser_azure.add_argument('--azure_subscription', help='Azure Subscription. If not specified, then available values will be displayed', required=False)
        parser_azure.add_argument('--azure_resource_group', help='Azure Resource Group. If not specified, then available values will be displayed', required=False)
        parser_azure.add_argument('--azure_workspace', help='Azure Workspace. If not specified, then available values will be displayed', required=False)
        parser_azure.add_argument('--enable_tracking_tags', action='store_true', help='Enable recording Azure specific information (like Azure Tenant ID, etc.) as asset tags', required=False)

        # Arguments required for Google Cloud Platform discovery
        parser_gcp = subparsers.add_parser ("gcp", help = "Discover Google Cloud Platform (GCP) instances")
        parser_gcp.add_argument('--enable_tracking_tags', action='store_true', help='Enable recording GCP specific information (like Project ID, etc.) as asset tags', required=False)

        # Arguments required for docker discovery 
        parser_docker = subparsers.add_parser ("docker", help = "Discover docker instances")
        parser_docker.add_argument('--image', help='The docker image (repo:tag) which needs to be inspected. If tag is not given, "latest" will be assumed.')
        parser_docker.add_argument('--containerid', help='The container ID of a running docker container which needs to be inspected.')
        parser_docker.add_argument('--assetid', help='A unique ID to be assigned to the discovered asset')
        parser_docker.add_argument('--assetname', help='A name/label to be assigned to the discovered asset')
        parser_docker.add_argument('--tmp_dir', help='Temporary directory to discover container', required=False)

        # Arguments required for Host discovery on Linux
        parser_linux = subparsers.add_parser ("host", help = "Discover linux host assets")
        parser_linux.add_argument('--remote_hosts_csv', help='CSV file containing details of remote hosts. CSV file column header [1st row] should be: hostname,userlogin,userpwd,privatekey,assetid,assetname. Note "hostname" column can contain hostname, IP address, CIDR range.')
        parser_linux.add_argument('--host_list', help='Same as the option: remote_hosts_csv. A file (currently in CSV format) containing details of remote hosts. CSV file column header [1st row] should be: hostname,userlogin,userpwd,privatekey,assetid,assetname. Note "hostname" column can contain hostname, IP address, CIDR range.')
        parser_linux.add_argument('--secure', action='store_true', help='Use this option to encrypt clear text passwords in the host list file')
        parser_linux.add_argument('--password', help='A password used to encrypt / decrypt login information from the host list file')
        parser_linux.add_argument('--assetid', help='A unique ID to be assigned to the discovered asset')
        parser_linux.add_argument('--assetname', help='A name/label to be assigned to the discovered asset')
        parser_linux.add_argument('--no_ssh_audit', action='store_true', help='Skip ssh audit')

        # Arguments required for nmap discovery
        parser_nmap = subparsers.add_parser ("nmap", help = "Discover assets using nmap")
        parser_nmap.add_argument('--hosts', help='A hostname, IP address or CIDR range', required=True)
        parser_nmap.add_argument('--no_ssh_audit', action='store_true', help='Skip ssh audit')

        # Arguments required for Repo discovery
        parser_repo = subparsers.add_parser ("repo", help = "Discover project repository as asset")
        parser_repo.add_argument('--repo', help='Local path or git repo url for project', required=True)
        parser_repo.add_argument('--type', choices=repo.SUPPORTED_TYPES, help='Type of open source component to scan for. Defaults to all supported types if not specified', required=False)
        parser_repo.add_argument('--level', help='Possible values {shallow, deep}. Shallow restricts discovery to 1st level dependencies only. Deep discovers dependencies at all levels. Defaults to shallow discovery if not specified', choices=['shallow','deep'], required=False, default='shallow')
        parser_repo.add_argument('--assetid', help='A unique ID to be assigned to the discovered asset')
        parser_repo.add_argument('--assetname', help='A name/label to be assigned to the discovered asset')
        # Switches related to secrets scan for repo
        parser_repo.add_argument('--secrets_scan', action='store_true', help='Perform a scan to look for secrets in the code')
        parser_repo.add_argument('--enable_entropy', action='store_true', help='Identify entropy based secrets')
        parser_repo.add_argument('--regex_rules_file', help='Path to JSON file specifying regex rules')
        parser_repo.add_argument('--check_common_passwords', action='store_true', help='Look for top common passwords.')
        parser_repo.add_argument('--common_passwords_file', help='Specify your own common passwords file. One password per line in file')
        parser_repo.add_argument('--include_patterns', help='Specify patterns which indicate files to be included in the secrets scan. Separate multiple patterns with comma.')
        parser_repo.add_argument('--include_patterns_file', help='Specify file containing include patterns which indicate files to be included in the secrets scan. One pattern per line in file.')
        parser_repo.add_argument('--exclude_patterns', help='Specify patterns which indicate files to be excluded in the secrets scan. Separate multiple patterns with comma.')
        parser_repo.add_argument('--exclude_patterns_file', help='Specify file containing exclude patterns which indicate files to be excluded in the secrets scan. One pattern per line in file.')
        parser_repo.add_argument('--mask_secret', action='store_true', help='Mask identified secret before storing for reference in ThreatWatch.')
        parser_repo.add_argument('--no_code', action='store_true', help='Disable storing code for reference in ThreatWatch.')

        # Arguments required for File-based discovery
        parser_file = subparsers.add_parser ("file", help = "Ingest asset inventory from file")
        parser_file.add_argument('--input', help='Absolute path to single input inventory file or a directory containing JSON files. Supported file formats are: PDF & JSON', required=True)
        parser_file.add_argument('--assetid', help='A unique ID to be assigned to the discovered asset. Defaults to input filename if not specified. Applies only for PDF files.')
        parser_file.add_argument('--assetname', help='A name/label to be assigned to the discovered asset. Defaults to assetid is not specified. Applies only for PDF files.')
        parser_file.add_argument('--type', choices=['repo'], help='Type of asset. Defaults to repo if not specified. Applies only for PDF files.', required=False, default='repo')

        # Arguments required for ServiceNow discovery
        parser_snow = subparsers.add_parser ("servicenow", help = "Ingest inventory from ServiceNow CMDB")
        parser_snow.add_argument('--snow_user', help='User name of ServiceNow account', required=True)
        parser_snow.add_argument('--snow_user_pwd', help='User password of ServiceNow account', required=True)
        parser_snow.add_argument('--snow_instance', help='ServiceNow Instance name', required=True)
        parser_snow.add_argument('--enable_tracking_tags', action='store_true', help='Enable recording ServiceNow specific information (like ServiceNow instance name, etc.) as asset tags', required=False)

        # Arguments required for docker CIS benchmarks 
        parser_docker_cis = subparsers.add_parser ("docker_cis", help = "Run docker CIS benchmarks")
        parser_docker_cis.add_argument('--assetid', help='A unique ID to be assigned to the discovered asset')
        parser_docker_cis.add_argument('--assetname', help='A name/label to be assigned to the discovered asset')
        parser_docker_cis.add_argument('--docker_bench_home', help='Location of docker bench CLI', default='.')

        # Arguments required for AWS CIS benchmarks
        parser_aws_cis = subparsers.add_parser ("aws_cis", help = "Run AWS CIS benchmarks")
        parser_aws_cis.add_argument('--aws_access_key', help='AWS access key', required=True)
        parser_aws_cis.add_argument('--aws_secret_key', help='AWS secret key', required=True)
        parser_aws_cis.add_argument('--assetid', help='A unique ID to be assigned to the discovered asset', required=True)
        parser_aws_cis.add_argument('--assetname', help='A name/label to be assigned to the discovered asset')
        parser_aws_cis.add_argument('--prowler_home', help='Location of cloned prowler github repo. Defaults to current directory', default='.')

        # Arguments required for Azure CIS benchmarks
        parser_az_cis = subparsers.add_parser("azure_cis", help = "Run Azure CIS benchmarks")
        parser_az_cis.add_argument('--assetid', help='A unique ID to be assigned to the discovered asset', required=True)
        parser_az_cis.add_argument('--assetname', help='A name/label to be assigned to the discovered asset')

        # Arguments required for GCP CIS benchmarks
        parser_gcp_cis = subparsers.add_parser("gcp_cis", help = "Run Google Cloud Platform CIS benchmarks")
        parser_gcp_cis.add_argument('--assetid', help='A unique ID to be assigned to the discovered asset', required=True)
        parser_gcp_cis.add_argument('--assetname', help='A name/label to be assigned to the discovered asset')

        # Arguments required for ssl audit 
        parser_ssl_audit = subparsers.add_parser ("ssl_audit", help = "Run SSL audit tests against your web URLs. Requires [twigs_ssl_audit] package to be installed")
        parser_ssl_audit.add_argument('--url', help='HTTPS URL', required=True)
        parser_ssl_audit.add_argument('--args', help='Optional extra arguments')
        parser_ssl_audit.add_argument('--info', help='Report LOW / INFO level issues', action='store_true')
        parser_ssl_audit.add_argument('--assetid', help='A unique ID to be assigned to the discovered web URL asset', required=True)
        parser_ssl_audit.add_argument('--assetname', help='Optional name/label to be assigned to the web URL asset')

        # Arguments required for web-app discovery and testing
        parser_webapp = subparsers.add_parser ("dast", help = "Discover and test web application using a DAST plugin")
        parser_webapp.add_argument('--url', help='Web application URL', required=True)
        parser_webapp.add_argument('--plugin', choices=['arachni', 'skipfish'], help='DAST plugin to be used. Default is arachni. Requires the plugin to be installed separately.', default='arachni')
        parser_webapp.add_argument('--pluginpath', help='Path where the DAST plugin is installed to be used. Default is /usr/bin.', default='/usr/bin')
        parser_webapp.add_argument('--args', help='Optional extra arguments to be passed to the plugin')
        parser_webapp.add_argument('--assetid', help='A unique ID to be assigned to the discovered webapp asset', required=True)
        parser_webapp.add_argument('--assetname', help='Optional name/label to be assigned to the webapp asset')


        args = parser.parse_args()

        logging_level = logging.INFO
        if args.quiet:
            logging_level = logging.ERROR
        # Setup the logger
        logging.basicConfig(filename=logfilename, level=logging.INFO, filemode='w', format='%(asctime)s %(levelname)-8s %(message)s', datefmt='%m/%d/%Y %I:%M:%S %p')
        console = logging.StreamHandler()
        console.setLevel(logging_level)
        console.setFormatter(logging.Formatter('%(levelname)-8s %(message)s'))
        logging.getLogger('').addHandler(console)

        # In insecure mode, we want to set verify=False for requests
        if args.insecure:
            GoDaddyCABundle = False

        logging.info('Started new run...')
        logging.debug('Arguments: %s', str(args))

        if args.handle is None:
            temp = os.environ.get('TW_HANDLE')
            if temp is None:
                logging.error('Error: Missing "--handle" argument and "TW_HANDLE" environment variable is not set as well')
                sys.exit(1)
            logging.info('Using handle specified in "TW_HANDLE" environment variable...')
            args.handle = temp

        if args.token is None:
            temp = os.environ.get('TW_TOKEN')
            if temp is not None:
                logging.info('Using token specified in "TW_TOKEN" environment variable...')
                args.token = temp

        if args.token is None and args.apply_policy is not None:
            logging.error('Error: Policy cannot be applied since "--token" argument is missing and "TW_TOKEN" environment variable is not set as well!')
            sys.exit(1)

        if args.instance is None:
            temp = os.environ.get('TW_INSTANCE')
            if temp is not None:
                logging.info('Using instance specified in "TW_INSTANCE" environment variable...')
                args.instance = temp
            elif args.token is not None:
                # missing instance but token is specified
                logging.error('Error: Missing "--instance" argument and "TW_INSTANCE" environment variable is not set as well')
                sys.exit(1)

#    if args.purge_assets == True and args.email_report == False:
#        logging.error('Purge assets option (--purge_assets) is used with Email report (--email_report)')
#        sys.exit(1)

        if (args.token is None or len(args.token) == 0) and args.out is None:
            logging.error('[token] argument is not specified and [out] argument is not specified. Unable to share discovered assets...exiting....')
            sys.exit(1)

        if args.schedule is not None and sys.platform != 'win32':
            from crontab import CronSlices
            # validate schedule
            if CronSlices.is_valid(args.schedule) == False:
                logging.error("Error: Invalid cron schedule [%s] specified!" % args.schedule)
                sys.exit(1)

        assets = []
        sub_pkg_list = ['ssl_audit']
        if args.mode in sub_pkg_list:
            assets = sub_pkg_get_inventory(args)
        elif args.mode == 'aws':
            assets = aws.get_inventory(args)
        elif args.mode == 'azure':
            assets = azure.get_inventory(args)
        elif args.mode == 'gcp':
            assets = gcp.get_inventory(args)
        elif args.mode == 'servicenow':
            assets = servicenow.get_inventory(args)
        elif args.mode == 'repo':
            assets = repo.get_inventory(args)
        elif args.mode == 'host':
            assets = linux.get_inventory(args)
        elif args.mode == 'nmap':
            assets = fingerprint.get_inventory(args)
        elif args.mode == 'docker':
            assets = docker.get_inventory(args)
        elif args.mode == 'file':
            assets = inv_file.get_inventory(args)
        elif args.mode == 'dast':
            assets = dast.get_inventory(args)
        elif args.mode == 'docker_cis':
            assets = docker_cis.get_inventory(args)
        elif args.mode == 'aws_cis':
            assets = aws_cis.get_inventory(args)
        elif args.mode == 'azure_cis':
            assets = azure_cis.get_inventory(args)
        elif args.mode == 'gcp_cis':
            assets = gcp_cis.get_inventory(args)
        elif args.mode == 'ssl_audit':
            assets = ssl_audit.get_inventory(args)

        exit_code = None
        if args.mode != 'host' or args.secure == False:
            if assets is None or len(assets) == 0:
                logging.info("No assets found!")
            else:
                """
                if args.asset_criticality is not None:
                    add_asset_criticiality_tag(assets, args.asset_criticality)
                """

                if args.tag_critical:
                    add_asset_criticality_tag(assets, '5')

                if args.tag:
                    add_asset_tags(assets, args.tag)

                if args.out is not None:
                    export_assets_to_file(assets, args.out)

                if args.token is not None and len(args.token) > 0:
                    asset_id_list = push_assets_to_TW(assets, args)

                pj_json = None
                if args.apply_policy is not None:
                    policy_job_name = policy_lib.apply_policy(args.apply_policy, asset_id_list, args)
                    while True:
                        time.sleep(60)
                        status, pj_json = policy_lib.is_policy_job_done(policy_job_name, args)
                        if status:
                            exit_code = policy_lib.process_policy_job_actions(pj_json)
                            break

                if args.token is not None and len(args.token) > 0:
                    run_scan(asset_id_list, pj_json, args)
            
                if args.schedule is not None and sys.platform != 'win32':
                    from crontab import CronTab
                    # create/update cron job
                    cron_cmd = sys.argv[0]
                    if "--handle" not in sys.argv:
                        cron_cmd = cron_cmd + " " + "--handle " + '"' + args.handle + '"'
                    if "--token" not in sys.argv:
                        cron_cmd = cron_cmd + " " + "--token " + '"' + args.token + '"'
                    if "--instance" not in sys.argv:
                        cron_cmd = cron_cmd + " " + "--instance " + '"' + args.instance + '"'
                    skip_next = False
                    for index in range(1, len(sys.argv)):
                        if sys.argv[index] == "--schedule":
                            skip_next = True
                            continue
                        if skip_next:
                            skip_next = False
                            continue
                        if sys.argv[index].startswith('-'):
                            cron_cmd = cron_cmd + " " + sys.argv[index]
                        else:
                            cron_cmd = cron_cmd + " " + '"' + sys.argv[index] + '"'
                    cron_comment = "TWIGS_" + args.mode
                    with CronTab(user=True) as user_cron:
                        # Find any existing jobs and remove those
                        ejobs = user_cron.find_comment(cron_comment)
                        for ejob in ejobs:
                            user_cron.remove(ejob)
                        njob = user_cron.new(command=cron_cmd, comment=cron_comment)
                        njob.setall(args.schedule)
                        logging.info("Added to crontab with comment [%s]", cron_comment)

        logging.info('Run completed...')
        if exit_code is not None:
            logging.info("Exiting with code [%s] based on policy evaluation", exit_code)
            sys.exit(exit_code)

    except Exception as e:
        logging.error("Something went wrong with this twigs run...")
        st = traceback.format_exc()
        logging.error("Exception trace details: %s", st)
        sys.exit(1)

if __name__ == '__main__':
    main()
