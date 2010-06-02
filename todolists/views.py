from django import forms

from django.http import HttpResponse, HttpResponseRedirect
from django.template import RequestContext
from django.core.mail import send_mail
from django.shortcuts import get_object_or_404, render_to_response
from django.contrib.auth.decorators import login_required, permission_required
from django.views.decorators.vary import vary_on_headers
from django.views.generic.create_update import delete_object
from django.template import Context, loader
from django.utils import simplejson

from main.models import Todolist, TodolistPkg, Package

class TodoListForm(forms.Form):
    name = forms.CharField(max_length=255,
            widget=forms.TextInput(attrs={'size': '30'}))
    description = forms.CharField(required=False,
            widget=forms.Textarea(attrs={'rows': '4', 'cols': '60'}))
    packages = forms.CharField(required=False,
            help_text='(one per line)',
            widget=forms.Textarea(attrs={'rows': '20', 'cols': '60'}))

    def clean_packages(self):
        package_names = [s.strip() for s in
                self.cleaned_data['packages'].split("\n")]
        package_names = set(package_names)
        packages = Package.objects.filter(
                pkgname__in=package_names).exclude(
                repo__testing=True).order_by('arch')
        return packages


@login_required
@vary_on_headers('X-Requested-With')
def flag(request, listid, pkgid):
    list = get_object_or_404(Todolist, id=listid)
    pkg  = get_object_or_404(TodolistPkg, id=pkgid)
    pkg.complete = not pkg.complete
    pkg.save()
    if request.is_ajax():
        return HttpResponse(
            simplejson.dumps({'complete': pkg.complete}),
            mimetype='application/json')
    return HttpResponseRedirect('/todo/%s/' % (listid))

@login_required
def view(request, listid):
    list = get_object_or_404(Todolist, id=listid)
    return render_to_response('todolists/view.html',
        RequestContext(request, {'list':list}))

@login_required
def list(request):
    lists = Todolist.objects.select_related('creator').order_by('-date_added')
    for l in lists:
        l.complete = TodolistPkg.objects.filter(
            list=l.id,complete=False).count() == 0
    return render_to_response('todolists/list.html',
            RequestContext(request, {'lists':lists}))

@permission_required('main.add_todolist')
def add(request):
    if request.POST:
        form = TodoListForm(request.POST)
        if form.is_valid():
            todo = Todolist.objects.create(
                creator     = request.user,
                name        = form.cleaned_data['name'],
                description = form.cleaned_data['description'])

            for pkg in form.cleaned_data['packages']:
                tpkg = TodolistPkg.objects.create(list = todo, pkg = pkg)
                send_todolist_email(tpkg)

            return HttpResponseRedirect('/todo/')
    else:
        form = TodoListForm()

    page_dict = {
            'title': 'Add Todo List',
            'form': form,
            'submit_text': 'Create List'
            }
    return render_to_response('general_form.html',
            RequestContext(request, page_dict))

@permission_required('main.change_todolist')
def edit(request, list_id):
    todo_list = get_object_or_404(Todolist, id=list_id)
    if request.POST:
        form = TodoListForm(request.POST)
        if form.is_valid():
            todo_list.name = form.cleaned_data['name']
            todo_list.description = form.cleaned_data['description']
            todo_list.save()

            packages = [p.pkg for p in todo_list.packages]

            # first delete any packages not in the new list
            for p in todo_list.packages:
                if p.pkg not in form.cleaned_data['packages']:
                    p.delete()

            # now add any packages not in the old list
            for pkg in form.cleaned_data['packages']:
                if pkg not in packages:
                    tpkg = TodolistPkg.objects.create(
                            list = todo_list, pkg = pkg)
                    send_todolist_email(tpkg)

            return HttpResponseRedirect('/todo/%d/' % todo_list.id)
    else:
        form = TodoListForm(initial={
            'name': todo_list.name,
            'description': todo_list.description,
            'packages': todo_list.package_names,
            })
    page_dict = {
            'title': 'Edit Todo List: %s' % todo_list.name,
            'form': form,
            'submit_text': 'Save List'
            }
    return render_to_response('general_form.html', RequestContext(request, page_dict))

@permission_required('main.delete_todolist')
def delete_todolist(request, object_id):
    return delete_object(request, object_id=object_id, model=Todolist,
            template_name="todolists/todolist_confirm_delete.html",
            post_delete_redirect='/todo/')

def send_todolist_email(todo):
    '''Sends an e-mail to the maintainer of a package notifying them that the
    package has been added to a todo list'''
    maints = todo.pkg.maintainers
    if not maints:
        return

    page_dict = {
            'pkg': todo.pkg,
            'todolist': todo.list,
            'weburl': 'http://www.archlinux.org'+ todo.pkg.get_absolute_url()
    }
    t = loader.get_template('todolists/email_notification.txt')
    c = Context(page_dict)
    send_mail('arch: Package [%s] added to Todolist' % todo.pkg.pkgname,
            t.render(c),
            'Arch Website Notification <nobody@archlinux.org>',
            [m.email for m in maints],
            fail_silently=True)


# vim: set ts=4 sw=4 et:
