"""
AutoSlugField taken from django-extensions at
748b452e4d2140b1c082e0b1544a0e4dc11d7a40
Uses Oscar's slugify function instead of Django's

Copyright (c) 2007 Michael Trier

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""

import re

from django.db.models import SlugField
from django.utils import six

from oscar.core.utils import slugify

try:
    from django.utils.encoding import force_unicode  # NOQA
except ImportError:
    from django.utils.encoding import force_text as force_unicode  # NOQA


MAX_UNIQUE_QUERY_ATTEMPTS = 100


class UniqueFieldMixin(object):

    def check_is_bool(self, attrname):
        if not isinstance(getattr(self, attrname), bool):
            raise ValueError("'{}' argument must be True or False".format(attrname))

    @staticmethod
    def _get_fields(model_cls):
        if hasattr(model_cls._meta, 'get_fields'):
            # verbosity due to replacement of deprecated model_cls._meta.get_fields_with_model(),
            # as explained here:
            # https://docs.djangoproject.com/en/1.9/ref/models/meta/#migrating-from-the-old-api
            return [
                (f, f.model if f.model != model_cls else None)
                for f in model_cls._meta.get_fields()
                if not f.is_relation
                    or f.one_to_one
                    or (f.many_to_one and f.related_model)
            ]
        else:
            # prior to 1.9
            return model_cls._meta.get_fields_with_model()

    def get_queryset(self, model_cls, slug_field):
        for field, model in self._get_fields(model_cls):
            if model and field == slug_field:
                return model._default_manager.all()
        return model_cls._default_manager.all()

    def find_unique(self, model_instance, field, iterator, *args):
        # exclude the current model instance from the queryset used in finding
        # next valid hash
        queryset = self.get_queryset(model_instance.__class__, field)
        if model_instance.pk:
            queryset = queryset.exclude(pk=model_instance.pk)

        # form a kwarg dict used to impliment any unique_together contraints
        kwargs = {}
        for params in model_instance._meta.unique_together:
            if self.attname in params:
                for param in params:
                    kwargs[param] = getattr(model_instance, param, None)

        new = six.next(iterator)
        kwargs[self.attname] = new
        while not new or queryset.filter(**kwargs):
            new = six.next(iterator)
            kwargs[self.attname] = new
        setattr(model_instance, self.attname, new)
        return new


class AutoSlugField(UniqueFieldMixin, SlugField):
    """ AutoSlugField

    By default, sets editable=False, blank=True.

    Required arguments:

    populate_from
        Specifies which field or list of fields the slug is populated from.

    Optional arguments:

    separator
        Defines the used separator (default: '-')

    overwrite
        If set to True, overwrites the slug on every save (default: False)

    Inspired by SmileyChris' Unique Slugify snippet:
    http://www.djangosnippets.org/snippets/690/
    """
    def __init__(self, *args, **kwargs):
        kwargs.setdefault('blank', True)
        kwargs.setdefault('editable', False)

        populate_from = kwargs.pop('populate_from', None)
        if populate_from is None:
            raise ValueError("missing 'populate_from' argument")
        else:
            self._populate_from = populate_from

        self.slugify_function = kwargs.pop('slugify_function', slugify)
        self.separator = kwargs.pop('separator', six.u('-'))
        self.overwrite = kwargs.pop('overwrite', False)
        self.uppercase = kwargs.pop('uppercase', False)
        self.check_is_bool('overwrite')
        self.allow_duplicates = kwargs.pop('allow_duplicates', False)
        self.check_is_bool('allow_duplicates')
        super(AutoSlugField, self).__init__(*args, **kwargs)

    def _slug_strip(self, value):
        """
        Cleans up a slug by removing slug separator characters that occur at
        the beginning or end of a slug.
        If an alternate separator is used, it will also replace any instances
        of the default '-' separator with the new separator.
        """
        re_sep = '(?:-|%s)' % re.escape(self.separator)
        value = re.sub('%s+' % re_sep, self.separator, value)
        return re.sub(r'^%s+|%s+$' % (re_sep, re_sep), '', value)

    def slugify_func(self, content):
        if content:
            return self.slugify_function(content)
        return ''

    def slug_generator(self, original_slug, start):
        yield original_slug
        for i in range(start, MAX_UNIQUE_QUERY_ATTEMPTS):
            slug = original_slug
            end = '%s%s' % (self.separator, i)
            end_len = len(end)
            if self.slug_len and len(slug) + end_len > self.slug_len:
                slug = slug[:self.slug_len - end_len]
                slug = self._slug_strip(slug)
            slug = '%s%s' % (slug, end)
            yield slug
        raise RuntimeError('max slug attempts for %s exceeded (%s)' %
            (original_slug, MAX_UNIQUE_QUERY_ATTEMPTS))

    def create_slug(self, model_instance, add):
        # get fields to populate from and slug field to set
        if not isinstance(self._populate_from, (list, tuple)):
            self._populate_from = (self._populate_from, )
        slug_field = model_instance._meta.get_field(self.attname)

        if add or self.overwrite:
            # slugify the original field content and set next step to 2
            slug_for_field = lambda field: self.slugify_func(getattr(model_instance, field))
            slug = self.separator.join(map(slug_for_field, self._populate_from))
            start = 2
        else:
            # get slug from the current model instance
            slug = getattr(model_instance, self.attname)
            # model_instance is being modified, and overwrite is False,
            # so instead of doing anything, just return the current slug
            return slug

        # strip slug depending on max_length attribute of the slug field
        # and clean-up
        self.slug_len = slug_field.max_length
        if self.slug_len:
            slug = slug[:self.slug_len]
        slug = self._slug_strip(slug)

        if self.uppercase:
            slug = slug.upper()

        original_slug = slug

        if self.allow_duplicates:
            return slug

        return super(AutoSlugField, self).find_unique(
            model_instance, slug_field, self.slug_generator(original_slug, start))

    def pre_save(self, model_instance, add):
        value = force_unicode(self.create_slug(model_instance, add))
        return value

    def get_internal_type(self):
        return "SlugField"

    def deconstruct(self):
        name, path, args, kwargs = super(AutoSlugField, self).deconstruct()
        kwargs['populate_from'] = self._populate_from
        if not self.separator == six.u('-'):
            kwargs['separator'] = self.separator
        if self.overwrite is not False:
            kwargs['overwrite'] = True
        if self.allow_duplicates is not False:
            kwargs['allow_duplicates'] = True
        return name, path, args, kwargs
