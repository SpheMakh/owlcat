#!/usr/bin/python
# -*- coding: utf-8 -*-

import os.path
import math
import sys
import re
import warnings

import numpy
import numpy.ma
import Owlcat
import matplotlib.pyplot as pyplot

from Owlcat import Parsing


#
# NB: these need to be constructed on-the-fly based on polarization info from the MS.
# Otherwise we don't support circular without kludging
#

# Each plotter func is a tuple of
#   label,description,callable,flags_only
# The 'callable' transforms data into plottables
# If 'flags_only' is False, callable(visibilities) should return an array of real plottables
# the same shape as the visibilities masked_array.
# If 'flags_only' is False, callable(flags,axis) should return an array of flag densities
# along the specified axis. The returned array should be reduced along 'axis'.
#
Plotters = [
  ("I","Stokes I",lambda data:(abs(data[...,0])+abs(data[...,3]))/2,False),
  ("Q","Stokes Q",lambda data:(abs(data[...,0])-abs(data[...,3]))/2,False),
  ("U","Stokes U",lambda data:(data[...,1].real+data[...,2].real)/2,False),
  ("V","Stokes V",lambda data:(data[...,1].imag-data[...,2].imag)/2,False),
  ("flags_I","I/Q flag density",
        lambda flags,meanaxis:
          (flags[...,0]|flags[...,3]).mean(meanaxis),True ),
  ("flags_all","2x2 flag density",
        lambda flags,meanaxis:
          (flags[...,0]|flags[...,1]|flags[...,2]|flags[...,3]).mean(meanaxis),True ),
];

for icorr,corr in enumerate(("XX","XY","YX","YY")):
  Plotters += [
    (corr,corr+" amplitude",lambda data,i=icorr:abs(data[...,i]),False ),
    (corr+"phase",corr+" phase",lambda data,i=icorr:numpy.ma.masked_array(numpy.angle(data[...,i]),
                                                                            data[...,i].mask),False ),
    (corr+"r",corr+" real",lambda data,i=icorr:data[...,i].real,False ),
    (corr+"i",corr+" imag",lambda data,i=icorr:data[...,i].imag,False ),
    ("flags_"+corr,corr+" flag density",
        lambda flags,meanaxis,i=icorr:
          flags[...,i].mean(meanaxis),True ),
 ];


if __name__ == "__main__":

  # setup some standard command-line option parsing
  #
  from optparse import OptionParser,OptionGroup
  parser = OptionParser(usage="""%prog: [options] MS column:plot [column:plot ...]""",
      description="""Makes plots of a column in the MS. Plots are specified as "column:plottype". If
column is not given, it defaults to the previous column, or CORRECTED_DATA. Use --list-plots to get
more information on available plots. If no plots are specified, CORRECTED_DATA:I is plotted by default.
""");

  parser.add_option("--list-plots",action="store_true",
                    help="list available plot types and exit");
  parser.add_option("-L","--channels",dest="freqslice",type="string",
                    help="channel selection: single number or start:end[:step] to select channels start through end-1, "
                    "or start~end[:step] to select channels start through end, with an optional stepping.");
  parser.add_option("-T","--timeslots",dest="timeslice",type="string",
                    help="timeslot selection by number, same format as channels.");
  parser.add_option("-I","--ifrs",dest="ifrs",type="string",
                    help="subset of interferometers to plot. Use \"-I help\" to get help on selecting ifrs.");
  parser.add_option("-D","--ddid",dest="ddid",type="string",
                    help="DATA_DESC_ID to plot. Default is first DDID found in MS. Use comma-separated list "
                    "(or 'all') to plot multiple DDIDs.");
  parser.add_option("-F","--field",dest="field",type="int",
                    help="FIELD_ID to plot. Default is all (this may produce some strange plots for multiple-field MSs.)");
  parser.add_option("-Q","--taql",dest="taql",type="string",
                    help="additional TaQL selection to restrict data subset.");
  parser.add_option("-f","--flagmask",metavar="FLAGS",dest="flagmask",type="string",
                    help="flagmask to apply to the BITFLAG column to get flagged data points"
                    "default is ALL, use 0 to ignore flags.");

  group = OptionGroup(parser,"Arranging your plots");
  group.add_option("--x-time",dest="xaxis",action="store_const",const=0,
                    help="use time for X axis and average over channels (default)");
  group.add_option("--x-freq",dest="xaxis",action="store_const",const=1,
                    help="use frequency for X axis and average over timeslots");
  
  PLOT,DDID,IFR = group_choices = [ "plot","ddid","ifr" ];
  group.add_option("-P","--page",metavar="|".join(group_choices),action="append",
                   type="choice",choices=group_choices,
                   help="put different plots, DDIDs and/or interferometers on "
                   "separate pages. Can be given multiple times, this will determine "
                   "page order. Default is to put DDIDs and plots on different pages, and to "
                   "stack interferometers.");
  group.add_option("-S","--stack",metavar="|".join(group_choices),action="append",
                   type="choice",choices=group_choices,
                   help="stack different plots, DDIDs and/or interferometers on "
                   "a single page. Can be given multiple times, this will determine "
                   "stacking order.");
  choices = group_choices[1:];
  group.add_option("-A","--average",metavar="|".join(choices),action="append",
                   type="choice",choices=choices,
                   help="average DDIDs and/or interferometers together. Use twice "
                   "to average both.");
  group.add_option("--ppp",dest="ppp",metavar="N",type="int",
                    help="maximum number of plots to stack per page. Default is 120.");
  group.add_option("--offset",dest="offset",metavar="X",type="float",
                    help="vertical offset between stacked plots, in units of the median stddev. "
                    "Default is 10. NB: for flag-density plots, unit is 0.11*maxdensity, so the "
                    "standard offset of 10 produces plots spaced at 110% of the max value.");
  parser.add_option_group(group);
  
  group = OptionGroup(parser,"Plot labels");
  group.add_option("--no-label-plot",dest="label_plot",action="store_false",
                    help="do not include plot type in labels (when stacking)");
  group.add_option("--no-label-ddid",dest="label_ddid",action="store_false",
                    help="do not include DDID in labels (when stacking)");
  group.add_option("--no-label-ifr",dest="label_ifr",action="store_false",
                    help="do not include IFR in labels (when stacking)");
  group.add_option("--no-label-baseline",dest="label_baseline",action="store_false",
                    help="do not include baseline length in labels");
  group.add_option("--no-label-mean",dest="label_mean",action="store_false",
                    help="do not include mean value in labels");
  group.add_option("--no-label-stddev",dest="label_stddev",action="store_false",
                    help="do not include standard deviation in labels");
  parser.add_option_group(group);

  group = OptionGroup(parser,"Output options");
  group.add_option("--title",dest="title",type="string",action="append",
                    help="provide a custom plot title. When you have multiple pages, you may "
                    "give this option serveral times.");
  group.add_option("-o","--output",metavar="FILE",dest="output",type="string",
                    help="plot output to FILE. If not specified, plot will be "
                    "shown in a window. File format is determined by extension, see matplotlib documentation "
                    "for supported formats. At least .png, .pdf, .ps, .eps and .svg are supported.");
  group.add_option("-r","--resolution",dest="resolution",type="int",metavar="DPI",
                    help="plot resolution for output to FILE (default is 100)");
  group.add_option("--size",metavar="WxH",dest="figsize",type="string",
                    help="set figure size, in cm. Default is '%default'.");
  group.add_option("--papertype",dest="papertype",type="string",
                    help="set paper type (for .ps output only.) Prefix with '/' for "
                    "landscape mode. Default is '%default', but can also use e.g. 'letter', 'a3', etc.");
  parser.add_option_group(group);


  parser.set_defaults(output="",xaxis=0,ddid='first',field=None,
    resolution=100,ppp=120,papertype='a4',figsize="21x29",offset=10,
    flag_mask=None,
    label_plot=True,
    label_ddid=True,
    label_ifr=True,
    label_baseline=True,
    label_mean=True,
    label_stddev=True,
    page=[],stack=[],average=[]);

  (options,args) = parser.parse_args();

  # print help on plotters
  if options.list_plots:
    print "Available plot types:\n"
    for p in Plotters:
      print "   %-16s%s"%(p[0],p[1]);
    print """
By default, specifying "DATA:XX" is the same as "DATA:XX.mean", producing a plot of mean |XX| 
in time or frequency (depending on choice of X axis). Use "DATA.mean:XX" to plot |mean XX| 
instead (i.e. mean visibilities, not mean amplitudes!) Use "DATA:XX.stddev" or "DATA.stddev:XX"
to plot standard deviations. Other options are ".sum", ".max" and ".min".

Note that flag-density plots do not require a data column to be specified, since the 
BITFLAG/FLAG columns are shared among all data columns.
""";
    sys.exit(0);
  
  # turn list of plotters into dict
  Plotters = dict([(p[0],p[1:]) for p in Plotters]);

  # figure out plot arrangements, insert defaults for things that are not specified explicitly
  allopt = options.page + options.stack + options.average;
  if PLOT not in allopt:
    options.page.insert(0,PLOT);
  if DDID not in allopt:
    options.page.insert(0,DDID);
  if IFR not in allopt:
    options.stack.insert(0,IFR);
  # check that there's no double entries
  for what in [PLOT,DDID,IFR]:
    if len([opt for opt in allopt if opt == what ]) > 1:
      parser.error("Conflicting --page/--stack/--average options for '%s'.");

  # get figsize
  try:
    w,h = map(int,options.figsize.split('x',1));
    figsize = (w*10,h*10);
  except:
    parser.error("Invalid --size setting '%s'"%options.figsize);

  # get MS name
  if not args:
    parser.error("MS not specified. Use '-h' for help.");
  msname = args[0]
  print "===> Attaching to MS %s"%msname;
  ms = Owlcat.table(msname);

  # get flagmask, use a Flagger for this
  import Owlcat.Flagger
  from Owlcat.Flagger import Flagger
  if options.flagmask == "0":
    flagmask = 0;
    print "===> Flagmask 0, ignoring all flags";
  elif options.flagmask is not None:
    flagger = Flagger(msname);
    flagmask = flagger.lookup_flagmask(options.flagmask);
    flagger.close();
    flagger = None;
    print "===> Flagmask is %s (you specified '%s')"%(Flagger.flagmaskstr(flagmask),options.flagmask);
    if not flagmask&Flagger.LEGACY:
      print "===> NB: legacy FLAG/FLAG_ROW columns will be ignored with this flagmask";
  else:
    flagmask = Flagger.BITMASK_ALL|Flagger.LEGACY;
    print "===> Using all flags";

  # parse slice specs
  try:
    freqslice = Parsing.parse_slice(options.freqslice);
    print "===> Channel selection is ",freqslice;
  except:
    parser.error("Invalid -L/--channels option. Start:end[:step] or start~end[:step] expected.");
  try:
    timeslice = Parsing.parse_slice(options.timeslice);
    print "===> Timeslot selection is ",timeslice;
  except:
    parser.error("Invalid -T/--timeslots option. Start:end[:step] start~end[:step] expected.");

  # make list of plots
  plots = [];
  has_flagplots = False;
  column0 = "CORRECTED_DATA";
  if column0 not in ms.colnames():
    column0 = "DATA";
  # go through list of arguments, or default list 
  for arg in (args[1:] or ["I"]):
    # parse as "[column[.reduce]:]plottype[.reduce]"
    m = re.match('^(\w+)(\.(\w+))?(:(\w+)(.(\w+))?)?$',arg);
    if not m:
      parser.error("'%s': invalid argument"%arg);
    g = m.groups();
    # groups 0-2 are the first field, 3-6 are the second field
    # if 3 didn't match, then we only have one field, which we treat as a plottype
    if g[3]:
      column,datareduce,plot,plotreduce = g[0],g[2],g[4],g[6];
    else:
      column,datareduce,plot,plotreduce = None,None,g[0],g[2];
    # the reduction function applies to visibilities or to plottable values
    if plotreduce and datareduce:
      parser.error("'%s': can't specify two .funcs"%arg);
    # check that function is valid
    for func in datareduce,plotreduce:
      if func not in [None,'mean','std','min','max','sum','product' ]:
        parser.error("'%s': '%s' is not a valid reduction function"%(arg,func));
    # lookup plotter
    plotdesc,func,is_flagplot = Plotters.get(plot,(None,None,None));
    if func is None:
      parser.error("'%s': unknown plot type '%s'"%(arg,plot));
    # more sanity checks
    if is_flagplot:
      if plotreduce or datareduce:
        parser.error("'%s': can't use '.%s' with flagplots"%(arg,(plotreduce or datareduce)));
      if column:
        parser.error("'%s': can't specify a column for flagplots"%arg);
    else:
      column0 = column or column0;
      if column0 not in ms.colnames():
        parser.error("MS does not contain a %s column"%column);
      # set default reduction, if nothing else is specified
      if not plotreduce and not datareduce:
        plotreduce = 'mean';
      plotdesc = " ".join(filter(bool,[datareduce,column0,plotreduce,plotdesc]));  
    # add to list of plots
    has_flagplots |= is_flagplot;
    plots.append((plot,plotdesc,func,is_flagplot,column0,datareduce,plotreduce));
 
  # collect applicable TaQL queries here
  taqls = [];

  # get IFR set
  import Meow.IfrSet
  ifrset = Meow.IfrSet.from_ms(ms);
  tot_ifrs = len(ifrset.ifrs());
  if options.ifrs:
    if options.ifrs == "help":
      # print help string, but trim away RTF tags
      print re.sub("<[^>]+>","",ifrset.subset_doc).replace("&lt;","<").replace("&gt;",">");
      sys.exit(0);
    ifrset = ifrset.subset(options.ifrs);
    taqls.append(ifrset.taql_string());
    print "===> Selected %d of %d interferometers "%(len(ifrset.ifrs()),tot_ifrs);
  else:
    print "===> Selected all %d interferometers "%tot_ifrs;

  # select DDIDs
  ddid_tab = Owlcat.table(ms.getkeyword('DATA_DESCRIPTION'));
  # default ('first') is to use first DDID in MS
  ddid_str = options.ddid.strip().lower();
  if ddid_str == 'first':
    ddids = [ ms.getcol('DATA_DESC_ID',0,1)[0] ];
    print "===> Using first DATA_DESC_ID (%d)"%ddids[0];
  # else see if it's a single int
  elif re.match('^\d+$',ddid_str):
    ddids = [ int(options.ddid) ];
  # else parse as list of ints, or 'all'. In this case we need extra info from the tables
  else:
    if ddid_str == "all": 
      ddids = range(ddid_tab.nrows());
    else:
      try:
        ddids = map(int,ddid_str.split(','));
      except:
        parser.error("Invalid -D/--ddid option: %s"%ddid_str);

  # Get data shapes by reading subtables. I used to just use:
  #     datashape = [ ms.nrows() ] + list(ms.getcoldesc('DATA')['shape']);
  # but this is not robust, since some MSs will not have a fixed-shape DATA column. So, read the subtables.
  spwids = ddid_tab.getcol('SPECTRAL_WINDOW_ID');
  polids = ddid_tab.getcol('POLARIZATION_ID');
  corrs  = Owlcat.table(ms.getkeyword('POLARIZATION')).getcol('CORR_TYPE');
  spw_tab = Owlcat.table(ms.getkeyword('SPECTRAL_WINDOW'));
  ref_freq = spw_tab.getcol('REF_FREQUENCY');
  nchan = spw_tab.getcol('NUM_CHAN');

  # select field
  if options.field is not None:
    taqls.append("FIELD_ID==%d"%options.field);
    print "===> Selecting FIELD_ID %d"%options.field;

  # select TaQL
  if options.taql:
    taqls.append(options.taql);
    print "===> Additional TaQL query is \"%s\""%options.taql;

  # apply accumulated selection
  if taqls:
    ms = ms.query("( " + " ) && ( ".join(taqls) + " )");
    print "===> Selected %d rows from MS"%ms.nrows();
  if not ms.nrows():
    print """MS selection is empty. You may have specified it incorrectly: please check your 
DATA_DESC_ID (option -D/--ddid), field (-F/--field), interferometer subset (-I/--ifrs) 
and/or TaQL query (-Q/--taql) options. Or was your MS empty to begin with?""";
    sys.exit(1);

  # get axis indices
  xaxis    = options.xaxis;
  meanaxis = 1 if xaxis==0 else 0;

  # figure out label format
  labels = [];
  if options.label_plot and PLOT in options.stack:
    labels.append("%(plot)s");
  if options.label_ddid and DDID in options.stack:
    labels.append("d#%(ddid)d");
  if options.label_ifr and IFR in options.stack:
    labels.append("%(ifr)s");
  if options.label_baseline and IFR in options.stack:
    labels.append("%(baseline)dm");
  if options.label_mean:
    labels.append("mean=%(mean).3g");
  if options.label_stddev:
    labels.append("std=%(stddev).3g");
  label_format = " ".join(labels);

  # Make list of trackkeys
  # A trackkey is (nplot,ddid,ifr); ddid or ifr is None if averaging 
  average_ddids = DDID in options.average;
  average_ifrs = IFR in options.average;
  # ranges for each trackkey
  keyranges = [ range(len(plots)),
                [None] if average_ddids else ddids,
                [None] if average_ifrs else [ (px[0],qx[0]) for px,qx in ifrset.ifr_index() ] ];

  import Owlcat.Plotting
  
  # A PlotCollection holds one set of plot tracks. 
  # This is a dict of trackkey:PC.
  # PC's are shared by multiple tracks  according to the page/stack/average settings.
  plotcolls = {};

  # Helper function to get a PC associated with the given trackkey
  # Creates one if it doesn't exist, and inserts it into the plotcolls dict
  # for every other track in the stacked set.
  def get_plot_collection (track):
    pc = plotcolls.get(track);
    if pc:
      return pc;
    # make new PlotCollection
    pc = plotcolls[track] = Owlcat.Plotting.PlotCollection(options);
#    print "New pc for",track;
    # associate it with every track that will go on it, by looping over stacked keys
    for plot1 in (keyranges[0] if PLOT in options.stack else [track[0]]):
      for ddid1 in (keyranges[1] if DDID in options.stack else [track[1]]):
        for ifr1 in (keyranges[2] if IFR in options.stack else [track[2]]):
          plotcolls[plot1,ddid1,ifr1] = pc;
#          print "also for ",(plot1,ddid1,ifr1);
    return pc;

  # set of IFRs for which (non-flagged) data is actually found
  active_ifrs  = set();
  labelattrs = {};  # dict of plot label attrs, updated in each loop

  # now loop over DDIDs
  for ddid in ddids:
    labelattrs['ddid'] = ddid;

    subms = ms.query("DATA_DESC_ID==%d"%ddid);
    datashape = [ subms.nrows(),nchan[spwids[ddid]],len(corrs[polids[ddid]]) ];

    print "===> Processing DATA_DESC_ID %d (%d MHz): %dx%d by %d rows"%(
            ddid,round(ref_freq[spwids[ddid]]*1e-6),datashape[1],datashape[2],datashape[0]);

    if not subms.nrows():
      continue;
    # get boolean flags
    if flagmask&Flagger.LEGACY:
      flagcol = subms.getcol('FLAG');
      # merge in FLAG_ROW column
      flagrowcol = subms.getcol('FLAG_ROW');
      flagcol |= flagrowcol[:,numpy.newaxis,numpy.newaxis];
    else:
      flagcol = numpy.zeros(datashape,bool);
    # get bitflags
    bitflags = flagmask&Flagger.BITMASK_ALL;
    if bitflags:
      if 'BITFLAG' in ms.colnames():
        bf = subms.getcol('BITFLAG');
        flagcol |= ((bf&bitflags)!=0); 
      if 'BITFLAG_ROW' in ms.colnames():
        bfr = subms.getcol('BITFLAG_ROW');
        flagcol |= ((bfr&bitflags)!=0)[:,numpy.newaxis,numpy.newaxis]; 
    flagcol = flagcol[:,freqslice,:];
    nf = flagcol.sum();
    print "===> %d of %d (%.2g%%) visibilities are flagged "%(nf,flagcol.size,(nf/float(flagcol.size))*100);

    # get antenna indices, and make dict per-ifr masks
    a1,a2 = subms.getcol('ANTENNA1'),subms.getcol('ANTENNA2');
    ifr_rows = dict([ ((p,q),numpy.where((a1==p)&(a2==q))[0]) for (p,plab),(q,qlab) in ifrset.ifr_index() ]);
    # visibility columns will be read into here, as demanded by the individual plots
    datacols = {};

    colname0 = None;
    for iplot,(plottype,plotdesc,plotfunc,is_flagplot,colname,datareduce,plotreduce) in enumerate(plots):
      if colname0 and colname and colname != colname0:
        labelattrs['plot'] = "%s:%s"%(colname,plottype);
      else:
        labelattrs['plot'] = plottype;
      colname0 = colname;
      print "===> Making plot",plotdesc;
      # read in data column, if not already read
      if colname:
        # do we already have a reduced version of the column cached?
        dc = datacols.get((colname,datareduce),None);
        if dc is None:
          # no, do we have an unreduced version cached?
          dc = datacols.get((colname,None),None);
          if dc is None:
            # ok, read & cache column
            dc = subms.getcol(colname)[:,freqslice,:];
            dc = datacols[colname,None] = numpy.ma.masked_array(dc,flagcol,fill_value=complex(0,0));
          # reduce and cache reduced version
          if datareduce:
            dc = datacols[colname,datareduce] = getattr(dc,datareduce)(meanaxis);

      # split up into per-baseline arrays, and accumulate data tracks
      for (p,plab),(q,qlab) in ifrset.ifr_index():
        idx = ifr_rows[p,q][timeslice];
        if not len(idx):
          continue;
        # this is the plot track ID. Things that we average over are replaced by None
        track = (iplot,None if average_ddids else ddid,None if average_ifrs else (p,q));
        # update label attributes
        labelattrs['baseline'] = round(ifrset.baseline(p,q));
        labelattrs['ifr'] = ifrset.ifr_label(p,q);
        # 
        if is_flagplot:
          d1 = numpy.ma.masked_array(plotfunc(flagcol[idx,...],meanaxis));
        else:
          d1 = plotfunc(dc[idx,...]);
          if plotreduce:
            d1 = getattr(d1,plotreduce)(meanaxis);
          d1.fill_value = 0;
        # check that this data track  is not fully flagged
        if d1.mask.all():
          continue;
        active_ifrs.add((p,q));
        # get a plot collection object, or make a new one
        plotcoll = get_plot_collection(track);
        # for flag-density plots, set the plot offset
        if is_flagplot:
          plotcoll.offset = max(plotcoll.offset,d1.max()*0.11*options.offset);
        # add or update track
        count = plotcoll.get_track_count(track);
        # count !=0 indicates track is being updated (because we're averaging DDIDs or IFRs)
        if count:
          d0 = plotcoll.get_track_data(track);
          if d0.shape != d1.shape:
            warnings.warn("Shape mismatch between averaged DDIDs or IFRs, will ignore mismatched data.");
            continue;
          # add data to track. For means, update as (old*n + new)/(n+1), but if stddev
          # reduction was in effect, update as sqrt((old**2)*n + new)/(n-1)
          if 'std' in (plotreduce,datareduce):
            d1 = numpy.sqrt(((d0**2)*count+d1**2)/(count+1));
          else:
            d1 = (d0*count+d1)/(count+1);
        # insert track data
        labelattrs['mean'] = mean = d1.mean();
        labelattrs['stddev'] = std = d1.std();
        plotcoll.add_track(track,d1,count=count+1,mean=mean,stddev=std,label=label_format%labelattrs);

  # deallocate datacubes
  datacols = flagcol = dc = None;

  # make list of active IFRS (as p,q pairs), sorted by baseline length
  print "===> Found data for %d interferometers"%len(active_ifrs);
  if not average_ifrs:
    keyranges[2] = sorted(active_ifrs,lambda a,b:cmp((round(ifrset.baseline(*a)),a),(round(ifrset.baseline(*b)),b)));

  if not active_ifrs:
    print "===> Nothing to be plotted. Check your data selection.";
    sys.exit(0);
    

  # Now, work out the order in which we plot the tracks and PlotCollections.
  # this is determined by the options.page and options.stack order

  # Form up list of all possible track keys, in the order in which they are
  # to be plotted. Note that after this first loop, the elements of each key are
  # scrambled, because of the way we build up the list...
  track_order = [[]];
  elorder = {};
  keyels = { PLOT:0,DDID:1,IFR:2 };
  for i,what in enumerate(options.average + options.stack + options.page):
    elorder[what] = i;
    track_order = [ to+[key] for key in keyranges[keyels[what]] for to in track_order ];
  # ...so unscamble them into proper order and turn them into tuples
  track_order = [ ( key[elorder[PLOT]],key[elorder[DDID]],key[elorder[IFR]] ) for key in track_order ];

  # figure out paper size
  landscape = options.papertype[0] == "/";
  papertype = options.papertype[1:] if landscape else options.papertype;

  # now, split this up into PlotCollections and the tracks associated with each
  pc_tracks = [];
  pc0 = None;
  for key in track_order:
    pc = plotcolls[key];
    # start new collection
    if pc is not pc0:
      pc_tracks.append((pc,[key]));
      pc0 = pc;
    # else key belongs to previous collection
    else:
      pc_tracks[-1][1].append(key);

  # Do we have multiple plots, or multiple pages per plot? Start a page counter then.
  if len(pc_tracks) > 1 or len(pc_tracks[0][1]) > options.ppp:
    ipage = 0;
  else:
    ipage = None;

  # loop over plotcollectors
  for iplotcoll,(plotcoll,keylist) in enumerate(pc_tracks):
    # do we have a title for this plot on the command line?
    if options.title and len(options.title) > iplotcoll:
      title0 = options.title[iplotcoll];
    # no, make default title
    else:
      nplot,ddid,ifr = keylist[0];
      chans = freqslice.indices(datashape[1]);
      if chans[1] == chans[0]+1:
        chandesc = str(chans[0]);
      elif chans[2] == 1:
        chandesc = "%d~%d"%(chans[0],chans[1]);
      else:
        chandesc = "%d~%d step %d"%(chans[0],chans[1]-1,chans[2]);
      title0 = msname;
      if average_ddids:
        title0 += " ddid %s"%"+".join(map(str,ddids));
      else:
        title0 += " ddid %d"%ddid;
      if options.freqslice:
        title0 += " (chan %s)"%chandesc;
      if average_ifrs:
        title0 += " mean of %d ifrs"%len(active_ifrs);
      elif IFR in options.page:
        title0 += " ifr %s (%dm)"%(ifrset.ifr_label(*ifr),round(ifrset.baseline(*ifr)));
      # plot description
      if PLOT in options.page:
        title0 += " "+plots[nplot][1];
    # split keylist if needed
    for nkey0 in range(0,len(keylist),options.ppp):
      nkey1 = min(nkey0+options.ppp,len(keylist));
      keys = keylist[nkey0:nkey1];
      # make filename, if saving file
      savefile = options.output;
      if ipage is not None:
        if savefile:
          basename,ext = os.path.splitext(savefile);  
          savefile = "%s.%d%s"%(basename,ipage,ext);
        ipage += 1;
      dual = (len(keys) > options.ppp/2);
      plotcoll.make_figure(keys,save=savefile,suptitle=title0,figsize=figsize,offset_std=options.offset,
          papertype=papertype,dpi=options.resolution,dual=dual,landscape=landscape);

  if not options.output:
    from pylab import plt
    plt.show();
