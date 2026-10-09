from decimal import Decimal
from django import forms
from .calculations import FIELDS, calculate
from .models import Method, Project


class RecordForm(forms.Form):
    detected_on = forms.DateField(label='检测日期', widget=forms.DateInput(attrs={'type': 'date'}))
    batch = forms.CharField(label='样品批号', max_length=100)
    reagent_batch = forms.CharField(label='试剂/标定批次', max_length=100, required=False)
    notes = forms.CharField(label='备注 / 修订理由', max_length=1000, required=False, widget=forms.Textarea(attrs={'rows': 3}))
    version = forms.IntegerField(widget=forms.HiddenInput(), initial=1)

    def __init__(self, method, *args, **kwargs):
        self.method = method
        self.values = []
        self.outputs = []
        super().__init__(*args, **kwargs)
        for i in (1, 2):
            for key, label, unit in FIELDS[method.kind]:
                self.fields[f'{key}_{i}'] = forms.DecimalField(label=f'{label}（{unit}）', required=False,
                    max_digits=24, decimal_places=12, widget=forms.NumberInput(attrs={'step': 'any'}))

    def clean(self):
        data = super().clean()
        for i in (1, 2):
            keys = [key for key, _, _ in FIELDS[self.method.kind]]
            present = [data.get(f'{key}_{i}') is not None for key in keys]
            if i == 2 and not any(present):
                continue
            if not all(present):
                raise forms.ValidationError(f'第 {i} 次测定的输入不完整，请补齐全部字段。')
            values = {key: data[f'{key}_{i}'] for key in keys}
            try:
                cleaned, output = calculate(self.method.kind, values, self.method.params)
                self.values.append(cleaned)
                self.outputs.append(output)
            except ValueError as exc:
                raise forms.ValidationError(str(exc)) from exc
        return data

    def input_groups(self):
        return [[self[f'{key}_{i}'] for key, _, _ in FIELDS[self.method.kind]] for i in (1, 2)]


class UploadForm(forms.Form):
    project = forms.ModelChoiceField(label='项目', queryset=Project.objects.none())
    workbook = forms.FileField(label='Excel 工作簿（.xlsx，最多 20 MB）')

    def __init__(self, projects, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['project'].queryset = projects

    def clean_workbook(self):
        file = self.cleaned_data['workbook']
        if not file.name.lower().endswith('.xlsx') or file.size > 20 * 1024 * 1024:
            raise forms.ValidationError('仅支持不超过 20 MB 的 .xlsx 文件')
        return file


class MethodForm(forms.Form):
    c = forms.DecimalField(label='标定浓度（mol/L）', min_value=Decimal('0.000000000001'), max_value=1000, decimal_places=12, max_digits=24, required=False)
    blank = forms.DecimalField(label='空白体积（mL）', min_value=0, max_value=100000, decimal_places=12, max_digits=24, required=False)
    basis = forms.CharField(label='核定依据 / 标定记录', max_length=2000, widget=forms.Textarea(attrs={'rows': 4}))
    confirmed = forms.BooleanField(label='我已核对当前算法、参数、单位与工作簿映射，可用于真实工作簿', required=False)
    version = forms.IntegerField(widget=forms.HiddenInput())

    def __init__(self, method, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if method.kind != 'titration':
            del self.fields['c']
            del self.fields['blank']
        else:
            self.fields['c'].required = True
            self.fields['blank'].required = True
