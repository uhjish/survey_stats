from collections import OrderedDict

def strip_line(l):
    # type: (str) -> str
    # strip whitespace and trailing period
    # and remove double quotes
    return l.strip().strip('.').replace('"','')


def parse_fwfcols_spss(spss_file):
    # type: (str) -> OrderedDict
    """Extracts dat metadata from CDC YRBS SPSS files

    Extracts the col_specs from a CDC YRBS SPSS file. The col_specs
    are given as 3-tuples of (var, start, end) for each column in
    the fixed-width dat file.

    Args:
        spss_file: SPSS file path or file object

    Returns:
        colspecs: an `OrderedDict` colnames as keys, (st,en) as vals
    """

    def parse_field_width(w):
        """ parse var, start and end """
        (var,span) = w.split(' ')[:2]
        (st,en) = span.split('-')
        return (var,int(st)-1,int(en))

    # if arg is filename, call self with open fh
    if not getattr(spss_file, 'read', False):
        with open(spss_file, 'r') as fh:
            return parse_fwfcols_spss(fh)

    col_specs = OrderedDict()
    widths_flag = False
    # extract fixed-width-field length rows
    for line in spss_file:
        if line.startswith('DATA LIST FILE'):
            widths_flag = True
            continue
        elif widths_flag and line.startswith('EXECUTE'):
            widths_flag = False
            break
        elif widths_flag:
            #parse a line with field widths
            #split on two consec spaces
            widths = strip_line(line).split('  ')
            for w in widths:
                (var, st, en) = parse_field_width(w)
                var = var.lower()
                col_specs[var] = (st,en)
            continue
        else:
            continue
    # - end for line in readline()...
    return col_specs


def parse_surveyvars_spss(spss_file):
    """Extracts dat metadata from CDC YRBS SPSS files

    Extracts the survey questions and responses from the
    CDC YRBS SPSS file.

    Args:
        spss_file: SPSS file path

    Returns:
        survey_vars: an OrderedDict with survey variables

        The resulting `survey_vars` is an OrderedDict with
        variable names as keys and dict values with fields of
        `question` containing the survey question, and `responses`
        containing a list of tuples of the form (resp_num, resp_label)
    """
    # if arg is filename, call self with open fh
    if not getattr(spss_file, 'read', False):
        with open(spss_file, 'r') as fh:
            return parse_surveyvars_spss(fh)

    survey_vars = OrderedDict()
    vars_flag = False
    vals_flag = False
    var = None
    vals = []
    # extract fixed-width-field length rows
    for line in spss_file:
        if line.startswith('VARIABLE LABELS'):
            vars_flag = True
            continue
        elif vars_flag and strip_line(line) == '':
            vars_flag = False
            continue
        elif vars_flag:
            #parse variable label
            # and add tuple (var, question/label)
            (var, q) =  strip_line(line).split(' ', 1)
            var = var.lower()
            survey_vars[var] = {
                'question': q,
                'responses': []
            }
        elif line.startswith('VALUE LABELS'):
            vals_flag = True
            vars_flag = False
            vals = []
            var = None
            continue
        elif vals_flag and line.startswith('/.'):
            #save the last var and val lbls
            #reset vals_flag
            survey_vars[var]['responses'] = vals[:]
            var = None
            vals = []
            vals_flag = False
            continue
        elif vals_flag and line.strip() == '/':
            # save the last var and val lbls
            # reset
            survey_vars[var]['responses'] = vals[:]
            var = None
            vals = []
            continue
        elif vals_flag and not var:
            # set the current var
            var = strip_line(line).lower()
            continue
        elif vals_flag and var:
            #add (num, label) to current list of val labels
            vals.append(tuple(
                strip_line(line).split(' ', 1) ))
            continue
        else:
            #default
            continue
    # - end for line in fh.readline()...
    return survey_vars





