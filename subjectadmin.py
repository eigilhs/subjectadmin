import asyncio
from requests_futures.sessions import FuturesSession
from functools import lru_cache
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
        self.session = FuturesSession(max_workers=24)
        self.auth(username, password)

    def auth(self, username, password):
        self.creds = (username, password)
        login_url = f'{self.devilry_url}/authenticate/login'
        r = self.session.post(login_url,
                          {'username': username, 'password': password},
                          allow_redirects=False).result()
        if not r.ok:
            raise ConnectionError('Auth failed')

    @staticmethod
    def _json_cb(sess, resp):
        resp.data = resp.json()

    def get(self, url, *, cb=None, **kwargs):
        cb = cb if not cb is None else self._json_cb
        return self.session.get(f'{self.rest_url}/{url}', **kwargs,
                                background_callback=cb)

    def post(self, url, **kwargs):
        return self.session.post(f'{self.rest_url}/{url}', **kwargs,
                                 auth=self.creds)

    def put(self, url, **kwargs):
        return self.session.put(f'{self.rest_url}/{url}', **kwargs,
                                auth=self.creds)

    def delete(self, url, **kwargs):
        return self.session.delete(f'{self.rest_url}/{url}', **kwargs,
                                   auth=self.creds)

    def periods(self):
        courses = self.get('allwhereisadmin').result().data
        periods = []
        for course in courses:
            for period in course['periods']:
                periods.append({'course': course, 'period': period})
        return periods

    def set_period(self, period):
        self.period = period

    @needs_period
    def create_assignment(self, *, short_name, long_name, first_deadline,
                          publishing_time, setupstudents_mode,
                          delivery_types=0, anonymous=False):
        post_data = locals()
        del post_data['self']
        post_data['first_deadline'] = first_deadline.strftime('%F %T')
        post_data['publishing_time'] = publishing_time.strftime('%F %T')
        post_data['period_id'] = self.period['id']
        return self.post('createnewassignment/', json=post_data,
                         background_callback=self._json_cb)

    def set_hard_deadlines(self, assignment_id):
        def task():
            r = self.get(f'assignment/{assignment_id}').result()
            assignment = r.data
            assignment['deadline_handling'] = 1
            r = self.put(f'assignment/{assignment_id}', json=assignment).result()
        return self.session.executor.submit(task)

    def set_points_assignment(self, assignment_id, min_points=0,
                              *, max_points, display_points=True):
        def task():
            r = self.get(f'assignment/{assignment_id}').result()
            points2grade = 'raw-points' if display_points else 'passed-failed'
            assignment = r.data
            assignment['max_points'] = max_points
            assignment['passing_grade_min_points'] = min_points
            assignment['points_to_grade_mapper'] = points2grade
            assignment['grading_system_plugin_id'] = \
                                    'devilry_gradingsystemplugin_points'
            r = self.put(f'assignment/{assignment_id}', json=assignment).result()
        return self.session.executor.submit(task)

    def examiner_stats(self, assignment_id):
        return self.get(f'examinerstats/{assignment_id}')

    def set_examiner(self, student, examiner, assignment):
        return self.post(f'group/{assignment}/',
                         json={'candidates': [{'user': {'id': student}}],
                               'examiners': [{'user': examiner['user']}],
                               'is_open': True})

    @lru_cache(maxsize=64)
    def find_person(self, username):
        r = self.session.get(f'{self.devilry_url}/devilry_usersearch/search'
                             f'?query={username}').result()
        if not r.ok:
            warn(f'Search could not be completed: {r.text}\n{r.reason}')
            return
        for user in r.json():
            if user['username'] == username:
                return user

    def set_tags(self, assignment):
        groups = self.get(f'group/{assignment}/').result().data
        students = self.get(f'relatedstudent_assignment_ro/{assignment}/')\
                       .result().data
        def get_tags(student):
            for st in students:
                if student == st['user']['id']:
                    return [{'tag': t} for t in st['tags'].split(',')]
        futures = []
        for group in groups:
            # TODO: Copy ALL tags
            f = self.put(f'group/{assignment}/',
                         json={'id': group['id'],
                               'candidates': [group['candidates'][0]],
                               'examiners': group['examiners'],
                               'is_open': True,
                               'tags': get_tags(group['candidates'][0]
                                                ['user']['id'])})
            futures.append(f)
        return futures

    def get_group(self, username, assignment):
        def cb(sess, resp):
            for group in resp.json():
                if group['candidates'][0]['user']['username'] == username:
                    resp.data = group
                    return
        return self.get(f'group/{assignment}/?query={username}', cb=cb)

    def update_examiner(self, group, examiner, assignment):
        examiners = [] if examiner is None else [{'user': examiner['user']}]
        return self.put(f'group/{assignment}/',
                        json={'id': group['id'],
                              'candidates': [group['candidates'][0]],
                              'examiners': examiners,
                              'is_open': True,
                              'tags': group['tags']})

    def remove_students(self, students, assignment):
        def remove(student):
            r = self.get(f'group/{assignment}/?query={student}').result()
            for group in r.data:
                if group['candidates'][0]['user']['username'] == student:
                    break
            else:
                return
            return self.delete(f'group/{assignment}/',
                               json={'id': group['id']}).result()
        return [self.session.executor.submit(remove, student)
                for student in students]

    def remove_students_by_tag(self, tag, assignment):
        r = self.get(f'group/{assignment}/?query={tag}').result()
        futures = []
        for group in r.data:
            if tag in map(lambda x: x['tag'], group['tags']):
                futures.append(self.delete(f'group/{assignment}/',
                                           json={'id': group['id']}))
        return futures

    def add_students(self, students, assignment):
        async def add(student):
            r = await asyncio.wrap_future(
                self.get(f"relatedstudent/{self.period['id']}?query={student}"))
            if not r.ok:
                warn(f'Student {student} could not be found:\n{r.text}')
            for stud in r.json():
                if stud['user']['username'] == student:
                    break
            stud_id = stud['user']['id']
            r = await asyncio.wrap_future(self.post(f'group/{assignment}/',
                          json={'candidates': [{'user': {'id': stud_id}}],
                                'is_open': True}))
            if not r.ok:
                warn(f'Student {student} could not be added:\n{r.text}')
        loop = asyncio.get_event_loop()
        g = asyncio.gather(*[add(student) for student in students])
        loop.run_until_complete(g)

    def setup_examiners_by_tags(self, assignment):
        r = self.get(f"relatedexaminer/{self.period['id']}").result()
        emap = {exr['tags']: exr['user']['id'] for exr in r.data}
        r = self.get(f'group/{assignment}/').result()
        futures = []
        for group in r.data:
            for tag in group['tags']:
                t = tag['tag']
                if not t in emap:
                    continue
                futures.append(self.update_examiner(group,
                                                    {'user': {'id': emap[t]}},
                                                    assignment))
        return futures

    def close_groups_without_deliveries(self, assignment):
        r = self.get(f'group/{assignment}/').result()
        futures = []
        for group in r.data:
            if group['num_deliveries'] == 0:
                futures.append(self.put(f'group/{assignment}/',
                                        json={'id': group['id'],
                                              'is_open': False,
                                              'candidates': group['candidates'],
                                              'examiners': group['examiners'],
                                              'tags': group['tags']}))
        return futures

    def set_deadline_text(self, assignment, text):
        dls = self.get(f'deadlinesbulk/{assignment}').result().data
        futures = []
        for dl in dls:
            if dl['text'] is None:
                futures.append(
            self.put(f"deadlinesbulk/{assignment}/{dl['bulkdeadline_id']}",
                     json={'text': text,
                           'deadline': dl['deadline']}))
        return futures

    def remove_examiner_no_delivery(self, assignment):
        r = self.get(f'group/{assignment}/').result()
        futures = []
        for group in r.data:
            if not group['num_deliveries']:
                futures.append(self.update_examiner(group, None,
                                                    assignment))

    @needs_period
    def points(self):
        ov = {}
        r = self.get(f"detailedperiodoverview/{self.period['id']}")
        data = r.result().data
        assignments = {a['id']: a['short_name'] for a in data['assignments']}
        for student in r.result().data['relatedstudents']:
            name = student['user']['username']
            stdict = {}
            for assignment in student['groups_by_assignment']:
                a_name = assignments[assignment['assignmentid']]
                if assignment['grouplist'] and assignment['grouplist'][0]['feedback']:
                    stdict[a_name] = assignment['grouplist'][0]['feedback']['points']
            stdict['group'] = student['relatedstudent']['tags']
            ov[name] = stdict
        return ov
