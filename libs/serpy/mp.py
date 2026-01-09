import serpy


def str_to_value(self, value):
    if value is None:
        return None
    return str(value)


def int_to_value(self, value):
    if value is None:
        return None
    return int(value)


def float_to_value(self, value):
    if value is None:
        return None
    return float(value)


class ChoicesField(serpy.Field):
    def __init__(self, choices_class=None, **kwargs):
        self.choices_class = choices_class
        super().__init__(**kwargs)

    def to_value(self, value):
        if value is None:
            return None

        if self.choices_class:
            try:
                label = self.choices_class(value).label
                return {"value": value, "label": label}
            except (ValueError, AttributeError):
                return {"value": value, "label": str(value)}

        if hasattr(self.instance, f"get_{self.attr}_display"):
            display_method = getattr(self.instance, f"get_{self.attr}_display")
            return {"value": value, "label": display_method()}

        return {"value": value, "label": str(value)}


serpy.StrField.to_value = str_to_value
serpy.IntField.to_value = int_to_value
serpy.FloatField.to_value = float_to_value
