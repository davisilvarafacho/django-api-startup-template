---
title: "How to scale a Django application to serve one million users?"
tipo: "source"
dominio: "tecnico"
tipo_fonte: "blog-post"
url: "https://tarekeesa7.medium.com/how-to-scale-a-django-application-to-serve-one-million-users-f3f4237660c8"
autor: "Tarek Eissa"
publicado: 2024-05-24
capturado: 2026-07-14
tags:
---
Wish your Django app could handle a million hits? This post is a compilation of articles, books, and videos I’ve read on how to take a Django application to its maximum capabilities, I’ve even implemented some of these recommendations myself.

It’s also a good time to remember that if your application is just starting out, you probably [shouldn’t obsess about its performance… yet](https://coffeebytes.dev/en/dont-obsess-about-your-web-application-performance/).

## Before we start! 🦸🏻

If you like this topic and you want to support me:

1. **Clap** my article 50 times; that will really help me out.👏
2. [**Follow**](https://medium.com/@tarekeesa7) me on [Medium](https://medium.com/@tarekeesa7) to get my latest article🫶

Lets get started…

## Reduce slow queries in Django

As you know, database access is usually the bottleneck of most applications. \*\*The most important action to take is to reduce the number of queries and the impact of each one of them. You can reduce the impact of your queries by 90%, and I am not exaggerating.

It is quite common to write code that occasions multiple queries to the database, as well as quite expensive searches.

Identify what queries are being made in your application using [django-debug-toolbar](https://github.com/jazzband/django-debug-toolbar) and reduce them, or make them more efficient:

- **select\_related()** to [avoid multiple searches in foreign key or one-to-one relationships](https://coffeebytes.dev/en/differences-between-select_related-and-prefetch_related-in-django/)
- **prefetch\_related()** to prevent excessive searches on many-to-many or many-to-one relationships
- **django\_annotate()** to add information to each object in a query. I have an entry where I explain [the difference between annotate and aggregate](https://coffeebytes.dev/en/django-annotate-and-aggregate-explained/).
- **django\_aggregate()** to process all information from a single query into a single data (summation, averages).
- **Object Q** to join queries by OR or AND directly from the database.
- F-Expressions\*\* to perform operations at the database level instead of in Python code.
![](https://miro.medium.com/v2/resize:fit:1400/format:webp/0*x4vzK1KgV3x7ht2s.png)

*Django debug tool bar showing the SQL queries of a Django request*

Example of use with *select\_related*.

```c
# review/views.py
from .models import Review

def list_reviews(request):
    queryset = Review.objects.filter(product__id=product_id).select_related('user') 
    # We're preventing a new query everytime we access review.user
    # ...
```

## Configure gunicorn correctly

Gunicorn is the most widely used Python WSGI HTTP server for Django applications. But it is not asynchronous, consider combining it with one of its asynchronous counterparts: hypercorn or uvicorn. The latter implements gunicorn workers.

## Configure gunicorn correctly

Make sure you are using the correct gunicorn workers, according to the number of cores in your processor. They recommend setting the workers to (2 x number of cores) + 1. According to the documentation, **with 4–12 workers you can serve from hundreds to thousands of requests per second**, so that should be enough for a medium to large scale website.

## Improve the performance of your serializers

If you use DRF and use its generic classes to create serializers, you may not exactly be getting the best performance. The generic classes for serializers perform data validation, which can be quite time consuming if you are only going to read data.

Even if you remembered to mark your fields as read\_only, DRF serializers are not the fastest, you might want to check out [Serpy](https://serpy.readthedocs.io/en/latest/), [Marshmallow](https://marshmallow.readthedocs.io/en/stable/). The topic is quite broad, but stay with the idea that there is a major area of improvement in Django serializers.

I leave you this article that explains [how some developers managed to reduce the time cost of serialization by 99%.](https://hakibenita.com/django-rest-framework-slow)

## Use pagination in your views

It probably sounds pretty obvious, yet I feel I should mention it: you don’t need to return an entire database table if your user only finds the first few records useful.

Use the *paginator* object provided by Django, or limit the results of a search to a few.

DRF also has an option to [paginate your results](https://www.django-rest-framework.org/api-guide/pagination/), check it out.

```c
# review/views.py
from django.views.generic import ListView
from .models import Review

class ReviewList(ListView): 
    model = Review 
    paginate_by = 25
    context_object_name = 'review_list'
```

## Use indexes in your models

Understand your more complex queries and try to create indexes for them. The index will make your searches in Django faster, but it will also slow down, slightly, the creations and updates of new information, besides taking up a little more space in your database. Try to strike a healthy balance between speed and storage space used.

```c
from django.db import models

class Review(models.Model):
    created = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )
```

## Use indexes for your searches

If your application makes heavy use of information searches, consider using an efficient [search engine, such as Solr](https://coffeebytes.dev/en/searches-with-solr-with-django-haystack/), rather than implementing the code yourself.

There are many options available:

- ElasticSearch
- Solr
- Whoosh
- Xapian

## Remove unused middleware

Each middleware implies an extra step in each web request, so removing all those middlewares that you do not use will mean a slight improvement in the response speed of your application.

Here are some common middleware that are not always used: messages, flat pages and localization, no, I don’t mean geographic location, but translating the content according to the local context.

```c
MIDDLEWARE = [
    # ...
    'django.contrib.messages.middleware.MessageMiddleware', 
    'django.contrib.flatpages.middleware.FlatpageFallbackMiddleware',
    'django.middleware.locale.LocaleMiddleware'
]
```

## Caching in Django

When the response time of your application becomes a problem, you should start caching all time-consuming and resource-intensive results.

Would you like to dig deeper into the caching system, I have a post about [caching in django using memcached](https://coffeebytes.dev/en/caching-in-django-rest-framework-using-memcached/) that you can check out to dig deeper.

If your page has too many models, and they rarely change, it does not make sense to access the database each time to request them with each new HTTP request. Just put the response of that request in cache and your response time will improve, this way every time the same content is requested, it will not be necessary to make a new request or calculations to the database, but the value will be returned directly from memory.

## Get Tarek Eissa’s stories in your inbox

Join Medium for free to get updates from this writer.

Among the options available are:

- Memcached
- Redis
- Database cache
- File system cache
```c
# settings.py
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.memcached.MemcachedCache',
        'LOCATION': '127.0.0.1:11211',
    }
}
```

The django cache is configurable at many, many levels, from the entire site to views or even small pieces of information.

```c
# myapp/views.py
from django.shortcuts import render
from django.views.decorators.cache import cache_page
```
```c
@cache_page(60*15)
def my_view(request):
    return render(request, 'myapp/template.html', {
        'time_consuming_data': get_time_consuming_data()
    })
```

Note that **memcached cache (memcached, redis) is an ephemeral storage method**, the entire cache will disappear if the system is rebooted or shutdown.

## Uses Celery for asynchronous tasks

Sometimes the bottleneck is the responsibility of third parties. When you send an email or request information from a third party, you have no way of knowing how long your request will take, a slow connection or an oversaturated server can keep you waiting for a response. There is no point in keeping the user waiting tens of seconds for an email to be sent, send them a reply back and transfer the email to a queue to be processed later. [Celery](https://docs.celeryproject.org/en/stable/) is the most popular way to do this.

No idea where to start, I have a couple of posts where I explain [how to run asynchronous tasks with celery and django](https://coffeebytes.dev/en/celery-and-django-to-run-asynchronous-tasks/).

```c
# myapp/views.py
from celery import shared_task

@shared_task
def send_order_confirmation(order_pk):
    email_data = generate_data_for_email(order_pk)
    send_customized_mail(**email_data)
```

## Partition the tables in your database

When your tables exceed millions of records, each search will go through the entire database, taking a very long time in the process. How could we solve this? By splitting the tables in parts so that each search is done on one of the parts, for example, one table for data from one year ago (or the period you prefer), another for data from two years ago and so on up to the first data.

The instructions for implementing partitioning depend on the database you are using. If you are using postgres this feature is only available for Postgres versions higher than 10. You can use [django-postgres-extra](https://django-postgres-extra.readthedocs.io/en/master/table_partitioning.html) to implement those extra features not found in the django ORM.

The implementation is too extensive and would require a full entry. There is an excellent article that explains how to implement [Postgresql partitioning in Django.](https://pganalyze.com/blog/postgresql-partitioning-django/)

Consider also looking into database replicas for reading files, depending on the architecture of your application, you can implement multiple replicas for reading and a master for writing. This approach is a whole topic and is beyond the scope of a short post, but now you know what to look for.

## Use a CDN (Content Delivery Network)

Serving static images and files can hinder the important part of your application; generating dynamic content. You can delegate the task of serving static content to a content delivery network (CDN).

In addition to benefiting from the geographic locations of CDNs; a server in the same country (or continent) as your user will result in a faster response.

There are many CDN options available, among the most popular options are AWS, [Azure](https://coffeebytes.dev/en/azure-az-900-certification-exam-my-experience/), Digital Ocean, Cloud Flare, among others.

## Denormalization

Sometimes there are quite costly runtime queries that could be solved by adding redundancy, repeated information. For example, imagine you want to return the number of products that have the phrase “for children” on your home page, running a query that searches for the word and then executes a count is fairly straightforward. But what if you have 10,000 or 100,000 or 1,000,000 products, every time you want to access the count value, your database will go through the entire table and count the data.

Instead of performing a count, you could store that number in the database or in memory and return it directly, to keep it updated you could use a periodic count or increment it with each addition.

Of course this brings the problem that you now have more data to maintain, not coupled together, so \*\*you should only use this option to solve your Django performance problems if you have already exhausted the other options.

```c
count = my_model.objects.filter(description__icontains="para niños").count() 
# ... denormalizing
count = my_count.objects.get(description="para niños") # Each row of the my_count model contains a description and the total results.
total_count = count.total
```

## Review the impact of third-party plugins

Sometimes our website works almost perfectly, but third party plugins, such as facebook analytics tools, google, social media chat integrations plugins affect the performance of our application. Learn how to delay their loading or modify them to reduce their impact, using async, defer or other HTML attributes, in combination with Javascript.

If the above is impossible, evaluate alternatives or consider eliminating them.

## Consider using another interpreter to improve django performance

It’s not all about the database, sometimes the problem is in the Python code itself.

In addition to the normal Python interpreter, the one offered by default on the official Python website, there are other interpreters that are sure to give you better performance.

[Pypy](https://www.pypy.org/) is one of them, it is responsible for optimizing Python code by analyzing the type of objects that are created with each execution. This option is ideal for applications where Django is in charge of returning a result that was mainly processed using Python code.

But not everything is wonderful; third-party interpreters, including pypy, are usually not 100% compatible with all Python code, but they are compatible with most of it, so, just like the previous option. \*\*Using a third-party interpreter should also be one of the last options you consider to solve your Django performance problem.

## Write bottlenecks in a low-level language with Swig

If you’ve tried all of the above and still have a bottlenecked application, you’re probably squeezing too much out of Python and need the speed of another language. But don’t worry, you don’t have to redo your entire application in C or C++. [Swig](http://www.swig.org/) allows you to create modules in C, C++, Java, Go or other lower level languages and import them directly from Python.

Do you want to know how much difference there is between Python and a compiled language like go, right?

If you have a bottleneck caused by some costly mathematical computation, which highlights the lack of speed of Python being an interpreted language, you may want to rewrite the bottleneck in some low-level language and then call it using Python. This way you will have the ease of use of Python with the speed of a low-level language.

Keep an eye on language Mojo, it promises to be a super set of Python but much faster

## ORMs and alternative frameworks

Depending on the progress of your application, you may want to migrate to another framework faster than Django. Django’s ORM is not exactly the fastest out there, and, at the time of writing, it is not asynchronous. You might want to consider giving [sqlalchemy](https://www.sqlalchemy.org/), [ponyorm](https://ponyorm.org/) a try.

Or, if your application is not very complex at the database level, you may want to write your own sql queries and combine them with some other framework.

The current trend is to separate frontend and backend, so Django is being used in conjunction with Django Rest Framework to create APIs, so if your plans include the creation of an API, you may want to consider FastAPI, if you don’t know it, take a look at my post where I explain [the basics of FastAPI](https://coffeebytes.dev/en/fastapi-tutorial-the-best-python-framework/).

## Bonus: applications with more than 63 000 models

There is a talk they gave at djangocon2019 where the speaker explains how they managed to deal with an application with 63000 endpoints, each with different permissions.

## Bonus: Technical blogs

Pinterest and Instagram are two gigantic sites that started out by choosing Django as their backend. You can find information about optimization and very specific problems in their technical blogs.

The instagram blog has a post called [Web Service efficiency at Instagram with Python](https://instagram-engineering.com/web-service-efficiency-at-instagram-with-python-4976d078e366), where they explain some problems encountered when handling 500 million users and how to fix them.

Here are the links to the blogs below:

- [Pinterest engineering](https://medium.com/pinterest-engineering)
- [Ingeniería de Instagram](https://instagram-engineering.com/)

References:

- Definitive Guide to Django: Web Development Done Right by Adrian Holovaty and Jacob Kaplan Moss
- Two scoops of Django 1.8 by Daniel Roy Greenfeld and Audrey Roy Greenfeld
- High performance Django by Peter Baumgartner and Yann Malet

> Thank you for reading! If you found this guide helpful, please give it a clap and follow me for more insightful content on real-time data analytics and web development with Django. Your support keeps me motivated to share more valuable resources. Let’s stay connected!

*Editor’s Note:* [*P*](https://heartbeat.comet.ml/) roSpexAi *is a contributor-driven online publication and community dedicated to providing premier educational resources for data science, machine learning, and deep learning practitioners. We’re committed to supporting and inspiring developers and engineers from all walks of life.*