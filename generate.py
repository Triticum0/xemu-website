#!/usr/bin/env python3
"""
xemu.app Static Site Generator
"""

import codecs
import json
import os
import re
import requests

from collections import defaultdict
from datetime import datetime, timezone
from functools import reduce
from github import Github
from jinja2 import Environment, FileSystemLoader
from tqdm import tqdm
from minify_html import minify as minify_html

output_dir = 'dist'
repo_url_base = 'https://raw.githubusercontent.com/mborgerson/xemu-website/master/'
compatibility_reports_url = 'https://reports.xemu.app/compatibility'
compatibility_reports_url_verify_certs = True
main_url_base = 'https://xemu.app'
# compatibility_reports_url = 'https://127.0.0.1/compatibility'
# compatibility_reports_url_verify_certs = False

develop_mode = False
disable_load_issues = develop_mode
disable_load_reports = develop_mode
disable_load_version = develop_mode

title_status_descriptions = {
    'Unknown'  : 'A compatibility test has not been recorded for this title.',
    'Broken'   : 'This title crashes very soon after launching, or displays nothing at all.',
    'Intro'    : 'This title displays an intro sequence, but fails to make it to gameplay.',
    'Starts'   : 'This title starts, but may crash or have significant issues.',
    'Playable' : 'This title is playable, with minor issues.',
    'Perfect'  : 'This title is playable from start to finish with no noticable issues.'
    }

def get_field(s,x):
    return s[x] if x in s else ''

class Issue:
    issues_by_title = defaultdict(list)
    all_issues = []

    def __init__(self, number, url, title, affected_titles, created_at, updated_at, closed_at, state):
        self.number = number
        self.url = url
        self.title = title
        self.affected_titles = affected_titles
        self.created_at = created_at
        self.updated_at = updated_at
        self.closed_at = closed_at
        self.state = state

    def __repr__(self):
        return self.title

    @classmethod
    def load_issues(cls, title_alias_map):
        """
        Search through all GitHub issues for any title tags to construct a
        list of titles and their associated issues
        """
        if disable_load_issues:
            return
        titles_re = re.compile(r'Titles?[:/]\s*([a-fA-f0-9,\s]+)', re.IGNORECASE)
        title_id_re = re.compile(r'([a-fA-f0-9]{8})')
        for issue in Github().get_user('mborgerson').get_repo('xemu').get_issues(state='all'):
            # Look for a titles sequence and pull out anything that looks like
            # an id
            references = ' '.join(titles_re.findall(issue.body or ''))
            affected_titles = title_id_re.findall(references)
            cls.all_issues.append(cls(
                issue.number,
                issue.html_url,
                issue.title,
                affected_titles,
                issue.created_at.replace(tzinfo=timezone.utc),
                issue.updated_at.replace(tzinfo=timezone.utc),
                issue.closed_at.replace(tzinfo=timezone.utc) if issue.state == 'closed' else None,
                issue.state))

        # Organize issues by title
        for issue in cls.all_issues:
            for title_id in issue.affected_titles:
                if title_id not in title_alias_map:
                    print('Warning: Issue %s references unknown title id "%s"' % (issue.url, title_id))
                    continue
                if issue not in cls.issues_by_title[title_alias_map[title_id]]:
                    cls.issues_by_title[title_alias_map[title_id]].append(issue)


class CompatibilityReport:
    all_reports = []
    reports_by_title = defaultdict(list)

    def __init__(self, info):
        self.info = info

    @property
    def created_at(self):
        return datetime.fromtimestamp(self.info['created_at'], timezone.utc)

    @classmethod
    def load_reports(cls, title_alias_map, url, verify):
        # FIXME: Ideally shouldn't load this all into memory. Instead, save to
        # disk and load on demand. But this works for now.
        if disable_load_reports:
            return
        cls.all_reports = [CompatibilityReport(i) for i in json.loads(requests.get(url, verify=verify).text)]
        for report in cls.all_reports:
            title_id = '%08x' % report.info['xbe_cert_title_id']
            if title_id not in title_alias_map:
                print('Warning: Compatibility report %s references unknown title "%s"' % (report.info['_id'], title_id))
                continue
            cls.reports_by_title[title_alias_map[title_id]].append(report)

class Title:
    def __init__(self, info_path):
        with open(info_path) as f:
            self.info = json.load(f)
        self.pubid = codecs.decode(self.info['title_id'][0:4], 'hex').decode('ascii')
        self.tid = '%03d' % (int(self.info['title_id'][4:], 16))
        self.title_name = self.info['name']
        anchor_text = ''.join([c if c.isalnum() else '-' for c in self.title_name])
        anchor_text = reduce(lambda s, c: s if (s.endswith('-') and c == '-') else s+c, anchor_text)
        anchor_text = anchor_text.lstrip('-').rstrip('-')
        self.title_url = f"/titles/{self.info['title_id']}#{anchor_text}"
        self.title_path = os.path.dirname(info_path)
        self.full_title_id_text = '%s-%s' % (self.pubid, self.tid)
        self.full_title_id_hex = self.info['title_id']
        self.full_title_id_num = int(self.info['title_id'], 16)

        # Determine cover paths
        self.have_cover = True
        self.cover_path = f'cover_front.jpg'
        if not os.path.exists(os.path.join(self.title_path, self.cover_path)):
            # Try .png extension
            self.cover_path = f'cover_front.png'
            if not os.path.exists(os.path.join(self.title_path, self.cover_path)):
                self.have_cover = False

        self.have_thumbnail = True
        self.cover_thumbnail_path = 'cover_front_thumbnail.jpg'
        if not os.path.exists(os.path.join(self.title_path, self.cover_thumbnail_path)):
            assert not self.have_cover, "Please create thumbnail for %s" % self.title_name
            self.have_thumbnail = False

        if self.have_cover:
            self.cover_url = repo_url_base + self.title_path + '/' + self.cover_path
        else:
            print('Note: Missing artwork for %s' % self.title_name)
            self.cover_url = repo_url_base + '/resources/cover_front_default.png'

        if self.have_thumbnail:
            self.cover_thumbnail_url = repo_url_base + self.title_path + '/' + self.cover_thumbnail_path
        else:
            if self.have_cover:
                print('Note: Missing thumbnail for %s' % self.title_name)
            self.cover_thumbnail_url = self.cover_url

        xtimage_path = os.path.join(self.title_path, 'xtimage.png')
        if os.path.exists(xtimage_path):
            self.xtimage_url = repo_url_base + self.title_path + '/xtimage.png'
        else:
            self.xtimage_url = None

    def process_compatibility(self):
        self.compatibility_tests = CompatibilityReport.reports_by_title[self.full_title_id_hex]
        if len(self.compatibility_tests) > 0:
            self.most_recent_test = sorted(self.compatibility_tests, key=lambda x:x.info['created_at'])[-1]
            self.status = self.most_recent_test.info['compat_rating']
            assert(self.status in title_status_descriptions)
        else:
            self.most_recent_test = None
            self.status = 'Unknown'
        assert(self.status in title_status_descriptions)

    @property
    def issues(self):
        """
        Open issues affecting this title.
        """
        return [i for i in Issue.issues_by_title[self.info['title_id']]
                if i.state == 'open']

    @property
    def recently_closed_issues(self):
        """
        Issues affecting this game that were closed recently (since last report) and may impact playability status.
        """
        if self.most_recent_test is None:
            return []
        return [i for i in Issue.issues_by_title[self.info['title_id']]
                if i.state != 'open' and self.most_recent_test.created_at < i.closed_at]


def main():
    env = Environment(loader=FileSystemLoader(searchpath='templates'))
    game_status_counts = {
        'Unknown'  : 0,
        'Broken'   : 0,
        'Intro'    : 0,
        'Starts'   : 0,
        'Playable' : 0,
        'Perfect'  : 0,
    }

    print('Gathering info.json files...')
    titles = []
    title_alias_map = {}
    title_lookup = {}
    for root, dirs, files in os.walk('titles', topdown=True):
        for name in files:
            if name != 'info.json': continue
            title = Title(os.path.join(root,name))
            titles.append(title)
            assert(title.full_title_id_hex not in title_lookup), "Title %s defined in multiple places" % title.full_title_id_hex
            title_lookup[title.full_title_id_hex] = title
            for release in title.info['releases']:
                title_alias_map[release['title_id']] = title.info['title_id']
    print('  - Found %d' % (len(titles)))

    print('Getting GitHub Issues List...')
    Issue.load_issues(title_alias_map)
    print('  - Ok. %d issues total' % len(Issue.all_issues))

    print('Getting compatibility report list...')
    CompatibilityReport.load_reports(
        title_alias_map,
        compatibility_reports_url,
        compatibility_reports_url_verify_certs
        )
    print('  - Ok. %d reports total' % len(CompatibilityReport.all_reports))
    for title in titles:
        title.process_compatibility()
        game_status_counts[title.status] += 1

    print('Rebuilding pages...')
    template = env.get_template('template_title.html')
    count = 0
    for title_id in tqdm(title_lookup):
        title_dir = os.path.join(output_dir, 'titles', title_id)
        os.makedirs(title_dir, exist_ok=True)
        title = title_lookup[title_id]
        with open(os.path.join(title_dir, 'index.html'), 'w') as f:
            f.write(minify_html(template.render(
                title=title,
                title_status_descriptions=title_status_descriptions,
                main_url_base=main_url_base
                )))
        count += 1
    print('  - Created %d title pages' % count)

    print('Generating alias redirects...')
    count = 0
    for title_id in title_alias_map:
        if title_alias_map[title_id] != title_id:
            # This is an alias, create a redirect
            title_dir = os.path.join(output_dir, 'titles', title_id)
            os.makedirs(title_dir, exist_ok=True)
            with open(os.path.join(title_dir, 'index.html'), 'w') as f:
                url=f"/titles/{title_alias_map[title_id]}"
                f.write(f'<html><head><meta http-equiv="refresh" content="0; URL={url!s}" /></head></html>')
            count += 1
    print('  - Created %d redirect pages' % count)

    if disable_load_version:
        xemu_build_tag = 'build-202106041913'
        xemu_build_version = '0.7.55'
        xemu_build_date = datetime(2021, 6, 4, 19, 13, 6)
    else:
        xemu_build_version = requests.get('https://raw.githubusercontent.com/mborgerson/xemu/ppa-snapshot/XEMU_VERSION').text
        latest_release = Github().get_user('mborgerson').get_repo('xemu').get_latest_release()
        xemu_build_date = latest_release.created_at

    print('Rebuilding index...')
    template = env.get_template('template_index.html')

    tmap = {t.full_title_id_num : t for t in titles}
    from rank import rank
    dorder = [tmap.pop(k) for k in rank]
    dorder.extend(sorted(tmap.values(),key=lambda title:title.title_name))

    with open(os.path.join(output_dir, 'index.html'), 'w') as f:
        f.write(minify_html(template.render(
            titles=dorder,
            title_status_descriptions=title_status_descriptions,
            game_status_counts=game_status_counts,
            xemu_build_version=xemu_build_version,
            xemu_build_date=xemu_build_date,
            main_url_base=main_url_base
            ), minify_js=True, minify_css=True))
    print('  - Ok')

    print('Building testing priority table')

    # Include titles that are either not Playable or have recently closed issues
    def filter_(t):
        if t.most_recent_test and t.most_recent_test.info['xemu_version'] == xemu_build_version:
            return False # Up to date
        if len(t.recently_closed_issues) > 0:
            return True # Make sure the issues described are fixed
        return t.status not in {'Playable', 'Perfect'}

    def rank(t):
        considered_playable = t.status in {'Playable', 'Perfect'}
        have_recently_closed_issues = len(t.recently_closed_issues) > 0
        ts = t.most_recent_test.created_at if t.most_recent_test else datetime.fromtimestamp(0, timezone.utc)
        return (not have_recently_closed_issues, considered_playable, ts)

    template = env.get_template('testing_priority.html')
    with open(os.path.join(output_dir, 'testing_priority.html'), 'w') as f:
        f.write(
            minify_html(
                template.render(
                    titles=sorted([t for t in titles if filter_(t)], key=rank)),
                minify_js=True, minify_css=True))
    print('  - Ok')

if __name__ == '__main__':
    main()
