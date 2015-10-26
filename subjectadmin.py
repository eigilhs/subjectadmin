import requests
from warnings import warn

def needs_period(method):
    def wrapper(self, *args, **kwargs):
        if self.period is not None:
            return method(self, *args, **kwargs)
        else:
            raise AttributeError('No period chosen')
    return wrapper

class SubjectAdmin:

    def __init__(self, *, username, password, devilry_url):
        self.devilry_url = devilry_url
        self.rest_url = f'{devilry_url}/devilry_subjectadmin/rest'
        self.period = None
        self.session = requests.Session()
        self.auth(username, password)

    def auth(self, username, password):
        self.creds = (username, password)
        login_url = f'{self.devilry_url}/authenticate/login'
        r = self.session.post(login_url,
                          {'username': username, 'password': password},
                          allow_redirects=False)
        self.cookies = r.cookies
        if not r.ok:
            raise ConnectionError('Auth failed')

    def get(self, url, **kwargs):
        return requests.get(f'{self.rest_url}/{url}',
                            cookies=self.cookies, **kwargs)

    def post(self, url, **kwargs):
        return requests.post(f'{self.rest_url}/{url}', cookies=self.cookies,
                             auth=self.creds, **kwargs)

    def put(self, url, **kwargs):
        return requests.put(f'{self.rest_url}/{url}',
                            cookies=self.cookies, auth=self.creds, **kwargs)

    def delete(self, url, **kwargs):
        return requests.delete(f'{self.rest_url}/{url}', cookies=self.cookies,
                               auth=self.creds, **kwargs)

    def periods(self):
        r = self.get('allwhereisadmin')
        periods = []
        for course in r.json():
            for period in course['periods']:
                periods.append({'course': course, 'period': period})
        return periods

    def set_period(self, period):
        self.period = period
        r = self.get(f"period/{period['id']}")
        # course_id = r.json()['breadcrumb'][-2]['id']
        # r = self.get(f'subject/{course_id}')
        # self.course = r.json()

    @needs_period
    def create_assignment(self, *, short_name, long_name, first_deadline,
                          publishing_time, setupstudents_mode,
                          delivery_types=0, anonymous=False):
        post_data = locals()
        del post_data['self']
        post_data['first_deadline'] = first_deadline.strftime('%F %T')
        post_data['publishing_time'] = publishing_time.strftime('%F %T')
        post_data['period_id'] = self.period['id']
        r = self.post('createnewassignment/', json=post_data)
        if r.ok:
            return r.json()
        warn(f'Could not create assignment: {r.text}\n{r.reason}')

    def set_hard_deadlines(self, assignment_id):
        r = self.get(f'assignment/{assignment_id}')
        if not r.ok:
            warn(f'Could not get assignment info: {r.reason}\n{r.text}')
            return
        assignment = r.json()
        assignment['deadline_handling'] = 1
        r = self.put(f'assignment/{assignment_id}', json=assignment)
        if not r.ok:
            warn(f'Could not update assignment: {r.text}\n{r.reason}')

    def set_points_assignment(self, assignment_id, min_points=0,
                              *, max_points, display_points=True):
        r = self.get(f'assignment/{assignment_id}')
        if not r.ok:
            warn(f'Could not get assignment info: {r.reason}\n{r.text}')
            return
        points2grade = 'raw-points' if display_points else 'passed-failed'
        assignment = r.json()
        assignment['max_points'] = max_points
        assignment['passing_grade_min_points'] = min_points
        assignment['points_to_grade_mapper'] = points2grade
        assignment['grading_system_plugin_id'] = \
                                'devilry_gradingsystemplugin_points'
        r = self.put(f'assignment/{assignment_id}', json=assignment)
        if not r.ok:
            warn(f'Could not update assignment: {r.text}\n{r.reason}')

    def examiner_stats(self, assignment_id):
        r = self.get(f'examinerstats/{assignment_id}')
        if r.ok:
            return r.json()
        warn(f'Couldn\'t get examiner stats:\n{r.reason}\n{r.text}')

    def set_examiner(self, student, examiner, assignment):
        r = self.post(f'group/{assignment}/',
                          json={'candidates': [{'user': {'id': student}}],
                                'examiners': [{'user': examiner['user']}],
                                'is_open': True})
        if not r.ok:
            warn(f"Could not set examiner {examiner['user']['username']}"
                 f" to student {student}.\n{r.reason}\n{r.text}")

    def find_person(self, username):
        r = requests.get(f'{self.devilry_url}/devilry_usersearch/search'
                         f'?query={username}', cookies=self.cookies)
        if not r.ok:
            warn(f'Search could not be completed: {r.text}\n{r.reason}')
            return
        for user in r.json():
            if user['username'] == username:
                return user

    def set_tags(self, assignment):
        r = self.get(f'group/{assignment}/')
        s = self.get(f'relatedstudent_assignment_ro/{assignment}/').json()
        def get_tags(student):
            for st in s:
                if student == st['user']['id']:
                    return [{'tag': t} for t in st['tags'].split(',')]
        for group in r.json():
            t = self.put(f'group/{assignment}/',
                         json={'id': group['id'],
                               'candidates': [group['candidates'][0]],
                               'examiners': group['examiners'],
                               'is_open': True,
                               'tags': get_tags(group['candidates'][0]['user']['id'])})
            if t.ok:
                print(f"Updated {group['candidates'][0]['user']['username']}")
            else:
                print(t.reason, t.text)


    def get_group(self, username, assignment):
        r = self.get(f'group/{assignment}/?query={username}')
        if not r.ok:
            warn(f'Search could not be completed: {r.text}\n{r.reason}')
            return
        for group in r.json():
            if group['candidates'][0]['user']['username'] == username:
                return group

    def update_examiner(self, group, examiner, assignment):
        examiners = [] if examiner is None else [{'user': examiner['user']}]
        r = self.put(f'group/{assignment}/',
                     json={'id': group['id'],
                           'candidates': [group['candidates'][0]],
                           'examiners': examiners,
                           'is_open': True,
                           'tags': group['tags']})
        if not r.ok:
            warn(f"Could not set examiner "
                 f"{examiner['user']} to group "
                 f"{group['id']}.\n{r.reason}\n{r.text}")

    def remove_students(self, students, assignment):
        for student in students:
            r = self.get(f'group/{assignment}/?query={student}')
            for group in r.json():
                if group['candidates'][0]['user']['username'] == student:
                    break
            else:
                return
            r = self.delete(f'group/{assignment}/', json={'id': group['id']})

    def remove_students_by_tag(self, tag, assignment):
        r = self.get(f'group/{assignment}/?query={tag}')
        for group in r.json():
            if tag in map(lambda x: x['tag'], group['tags']):
                r = self.delete(f'group/{assignment}/', json={'id': group['id']})

    def add_students(self, students, assignment):
        for student in students:
            r = self.get(f"relatedstudent/{self.period['id']}?query={student}")
            if not r.ok:
                warn(f'Student {student} could not be found:\n{r.text}')
            for stud in r.json():
                if stud['user']['username'] == student:
                    break
            stud_id = stud['user']['id']
            r = self.post(f'group/{assignment}/',
                          json={'candidates': [{'user': {'id': stud_id}}],
                                'is_open': True})
            if not r.ok:
                warn(f'Student {student} could not be added:\n{r.text}')

    def setup_examiners_by_tags(self, assignment):
        r = self.get(f"relatedexaminer/{self.period['id']}")
        emap = {exr['tags']: exr['user']['id'] for exr in r.json()}
        r = self.get(f'group/{assignment}/')
        for group in r.json():
            for tag in group['tags']:
                t = tag['tag']
                if not t in emap:
                    continue
                self.update_examiner(group, {'user': {'id': emap[t]}}, assignment)

    def close_groups_without_deliveries(self, assignment):
        NotImplemented

    def set_deadline_text(self, assignment, text):
        r = self.get(f'deadlinesbulk/{assignment}')
        dls = r.json()
        for dl in dls:
            if dl['text'] is None:
                r = self.put(f"deadlinesbulk/{assignment}/{dl['bulkdeadline_id']}",
                             json={'text': text,
                                   'deadline': dl['deadline']})
                if not r.ok:
                    warn(f'Could not set deadline text: {r.reason}\n{r.text}')

    def remove_examiner_no_delivery(self, assignment):
        r = self.get(f'group/{assignment}/')
        for group in r.json():
            if not group['num_deliveries']:
                self.update_examiner(group, None, assignment)
