def conetar_subclasses_signal(model, signal, *handlers):
    for handler in handlers:
        signal.connect(handler, sender=model)
        for submodel in model.__subclasses__():
            signal.connect(handler, sender=submodel)


def desconetar_subclasses_signal(model, signal, *handlers):
    for handler in handlers:
        signal.disconnect(handler, sender=model)
        for submodel in model.__subclasses__():
            signal.disconnect(handler, sender=submodel)
