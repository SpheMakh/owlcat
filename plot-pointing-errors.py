#!/usr/bin/python
# -*- coding: utf-8 -*-

if __name__ == "__main__":

  # setup some standard command-line option parsing
  #
  from optparse import OptionParser,OptionGroup
  from Owlcat.Plotting import MultigridPlot,SkyPlot,PLOT_SINGLE,PLOT_MULTI,PLOT_ERRORBARS

  parser = OptionParser(usage="""%prog: [plots & options] parmtables""",
      description="""Makes various plots of dE solutions.""");

  parser.add_option("-l","--list",action="store_true",
                    help="lists stuff found in MEP tables, then exits");
  parser.add_option("-c","--cache",metavar="FILENAME",type="string",
                    help="cache parms to file, which can be fed to plot-de-solutions script");
  parser.add_option("-n","--nominals",metavar="FILENAME",type="string",
                    help="includes nominal offsets on plot (supply filename)");
  parser.add_option("--nominal-circle",metavar="mDEG",type="float",default=10,
                    help="size of circle around nominal offsets");
  parser.add_option("--from",metavar="TIMESLOT",type="int",default=0,
                    help="extract solutions starting from given timeslot");
  parser.add_option("--to",metavar="TIMESLOT",type="int",default=-1,
                    help="extract solutions until the given timeslot");
  parser.add_option("--pt-max",metavar="VALUE",type="float",default=0,
                    help="set fixed plot limits on pointing error vs. time plot");
  parser.add_option("--ft",action="store_true",
                    help="generate plots of pointing offset Fourier components");

  plotgroup = OptionGroup(parser,"Plotting options");
  outputgroup = OptionGroup(parser,"Output options");
  MultigridPlot.init_options(plotgroup,outputgroup);
  SkyPlot.init_options(plotgroup,outputgroup);

  parser.add_option_group(plotgroup);
  parser.add_option_group(outputgroup);

  (options,args) = parser.parse_args();

  if not args:
    parser.error("No parmtables specified.");

  import os.path
  import os
  import sys

  import numpy
  from Owlcat.ParmTables import ParmTab
  import math
  DEG = math.pi/180;
  ARCMIN = math.pi/(180*60);

  SPWS = range(len(args));

  # set of all sources, antennas and correlations
  ANTS = set();

  # complex array of dEs per each src,antenna,corr tuple
  des = {};

  # scan funklet names to build up sets of keys
  oldtable = False;
  pt = ParmTab(args[0]);
  for name in pt.funklet_names():
    if name.startswith("E::dlm::dl"):
      oldtable = True;
    if name.startswith("E::dlm::dl:") or name.startswith("E::dl:"):
      fields = name.split(':');
      ANTS.add(fields[-1]);
  ts_slice = slice(getattr(options,'from'),options.to if options.to >=0 else None);
  NTIMES = len(pt.funkset(pt.funklet_names()[0]).get_slice()[ts_slice]);
  print "%d timeslots found in table %s"%(NTIMES,args[0]);

  ANTS = sorted(ANTS);
  if options.list:
    print "MEP table %s contains pointing offsets for"%args[0];
    print "%d antennas: %s"%(len(ANTS)," ".join(ANTS));
    sys.exit(0);

  interval = None;

  def c00 (funklet):
    global interval;
    if interval is None:
      interval = funklet.domain.time[1] - funklet.domain.time[0];
    if numpy.isscalar(funklet.coeff):
      return funklet.coeff;
    else:
      return funklet.coeff.ravel()[0];

  # dl,dm is a 2 x NSPW x  NANT x NTIME array of poitning offsets
  dlm = numpy.zeros((2,len(SPWS),len(ANTS),NTIMES),dtype=float);

  # bsz is a 2 x 2 x NSPW x  NANT x NTIME array of beam sizes
  # first index is x/y, second is l/m
  bsz = numpy.zeros((2,2,len(SPWS),len(ANTS),NTIMES),dtype=float);
  beam_sizes = 0;
  
  for spw,tabname in enumerate(args):
    print "Reading",tabname;
    pt = ParmTab(tabname);
    for i,ant in enumerate(ANTS):
      # fill dlm
      if oldtable:
        fsl = pt.funkset('E::dlm::dl:%s'%ant).get_slice()[ts_slice];
        fsm = pt.funkset('E::dlm::dm:%s'%ant).get_slice()[ts_slice];
      else:
        fsl = pt.funkset('E::dl:%s'%ant).get_slice()[ts_slice];
        fsm = pt.funkset('E::dm:%s'%ant).get_slice()[ts_slice];
      if len(fsl) != len(fsm) or len(fsl) != NTIMES:
        print "Error: table contains %d funklets for dl and %d for dm; %d expected"%(len(fsl),len(fsm),NTIMES);
        sys.exit(1);
      dlm[0,spw,i,:] = map(c00,fsl);
      dlm[1,spw,i,:] = map(c00,fsm);
      # fill beam sizes
      if 'E::beamshape:%s'%ant in pt.funklet_names():
        beam_sizes = 1;
        fs = pt.funkset('E::beamshape:%s'%ant).get_slice()[ts_slice];
        bsz[0,0,spw,i,:] = map(c00,fs);
      elif 'E::beamshape:xy:lm:%s'%ant in pt.funklet_names():
        beam_sizes = 4;
        for ixy,xy in enumerate("xy"):
          for ilm,lm in enumerate("lm"):
            fs = pt.funkset('E::beamshape:%s:%s:%s'%(ant,xy,lm)).get_slice()[ts_slice];
            bsz[ixy,ilm,spw,i,:] = map(c00,fs);
  
  interval = round((interval or 60)/60);
  print "Solution interval is",interval,"minutes; total time",(interval*NTIMES)/60,"hours";

  # write cache
  if options.cache:
    import cPickle
    cachefile = options.cache+'.cache';
    cPickle.dump((dlm,bsz,beam_sizes),file(cachefile,'w'));
    print "Cached all structures to file",cachefile;

  # convert dlm to millidegrees
  dlm0 = dlm.copy();
  dlm *= 180*1000/math.pi;

  # take mean and std along freq axis
  # these are now 2 x NANT x NTIME arrays
  dlm_mean = dlm.mean(1);
  dlm_std  = dlm.std(1);
  bsz_mean = bsz.mean(2);
  bsz_std  = bsz.std(2);
  # and along time axis
  # these are now 2 x NSPW x NANT
  dlm_fmean = dlm.mean(3);
  dlm_fstd  = dlm.std(3);
  bsz_fmean = bsz.mean(4);
  bsz_fstd  = bsz.std(4);

  print "Read %d parmtables"%len(args);

  from Owlcat.Plotting import MultigridPlot,PLOT_SINGLE,PLOT_MULTI,PLOT_ERRORBARS,PLOT_BARPLOT

  # initialize plot object
  figplot = MultigridPlot(options);
  make_figure = figplot.make_figure;

  skyplot = SkyPlot(options);
  make_skymap = skyplot.make_figure;


  funcs = [
    lambda iant:(dlm_mean[0,iant,:],dlm_std[0,iant,:]),
    lambda iant:("mean %.2f"%numpy.array([dlm_mean[0,iant,:].mean()]),
                 "+/- %.2f"%numpy.array([dlm_mean[0,iant,:].std()])),
    lambda iant:(dlm_mean[1,iant,:],dlm_std[1,iant,:]),
    lambda iant:( "mean %.2f"%numpy.array([dlm_mean[1,iant,:].mean()]),
                  "+/- %.2f"%numpy.array([dlm_mean[1,iant,:].std()])),
    lambda iant:(dlm_fmean[0,:,iant],dlm_fstd[0,:,iant]),
    lambda iant:(dlm_fmean[1,:,iant],dlm_fstd[1,:,iant])
  ];

  make_figure(enumerate(("dl","","dm","","dl, freq","dm, freq")),enumerate(ANTS),
        lambda i,iant:funcs[i](iant),
      hline=0,ylock=(-options.pt_max,options.pt_max) if options.pt_max else True,figsize=(290,150),mode=PLOT_ERRORBARS,
      suptitle="Pointing offset mean & stddev across all bands (top two plots) and times (bottom two plots), mdeg",
      save="Epnt_mean");

  for iant,ant in enumerate(ANTS):
    print "mean offset %s: %6.2f %6.2f"%(ant,dlm_mean[0,iant,:].mean(),dlm_mean[1,iant,:].mean());

  if options.ft:
    import numpy.fft
    ft_slice = slice(1,NTIMES/2+1);
    # fft scaling is 1/NTIMES, then we multiply by 2 since each fourier components
    # is present as its own conjugate
    dlm_fft = abs(numpy.fft.fft(dlm_mean))[:,:,ft_slice]/(NTIMES/2.);
    periods = 1/abs(numpy.fft.fftfreq(NTIMES,interval)[ft_slice]);
    dlm_fftmax = dlm_fft.max(2);
    dlm_fftper = numpy.zeros((2,len(ANTS)));
    for i in 0,1:
      for iant in range(len(ANTS)):
        dlm_fftper[i,iant] = periods[numpy.where(dlm_fft[i,iant,:] == dlm_fftmax[i,iant])]
    funcs = [
      lambda iant:dlm_fft[0,iant,:],
      lambda iant:("max %.1f"%dlm_fftmax[0,iant],"@%d min"%dlm_fftper[0,iant]),
      lambda iant:dlm_fft[1,iant,:],
      lambda iant:("max %.1f"%dlm_fftmax[1,iant],"@%d min"%dlm_fftper[1,iant]),
    ];
    labels = [ "FT(dl)","","FT(dm)","" ];
    make_figure(enumerate(labels),enumerate(ANTS),
          lambda i,iant:funcs[i](iant),
        hline=0,ylock=(0,dlm_fft.max()),figsize=(290,25*len(labels)),mode=PLOT_BARPLOT,
        suptitle="Pointing offset Fourier components",
        save="Epnt_ft");


  if beam_sizes == 4:
    funcs = [];
    for i0 in range(2):
      for j0 in range(2):
        funcs += [
          lambda iant,i=i0,j=j0:(bsz_mean[i,j,iant,:],bsz_std[i,j,iant,:]),
          lambda iant,i=i0,j=j0:("mean %.2f"%numpy.array([bsz_mean[i,j,iant,:].mean()]),
                        "+/- %.2f"%numpy.array([bsz_mean[i,j,iant,:].std()]))
        ];

    make_figure(enumerate(("Lx","","Mx","","Ly","","My","")),enumerate(ANTS),
          lambda i,iant:funcs[i](iant),
        hline=1,ylock=True,figsize=(290,210),mode=PLOT_ERRORBARS,
        mean_format="%.4f",
        suptitle="Beam extent in L/M, for X and Y dipoles, mean over time",
        save="Eshape_mean");

    funcs = [];
    for i0 in range(2):
      for j0 in range(2):
        funcs += [
          lambda iant,i=i0,j=j0:(bsz_fmean[i,j,:,iant],bsz_fstd[i,j,:,iant]),
          lambda iant,i=i0,j=j0:("mean %.2f"%numpy.array([bsz_fmean[i,j,:,iant].mean()]),
                        "+/- %.2f"%numpy.array([bsz_fmean[i,j,:,iant].std()]))
        ];
    make_figure(enumerate(("Lx","","Mx","","Ly","","My","")),enumerate(ANTS),
          lambda i,iant:funcs[i](iant),
        mean_format="%.4f",
        hline=1,ylock=True,figsize=(290,210),mode=PLOT_ERRORBARS,
        suptitle="Beam extent in L/M, for X/Y dipoles, mean over frequency",
        save="Eshape_mean_fq");
  elif beam_sizes == 1:
    funcs = [
      lambda iant:(bsz_mean[0,0,iant,:],bsz_std[0,0,iant,:]),
      lambda iant:( "mean %.2f"%numpy.array([bsz_mean[0,0,iant,:].mean()]),
                    "+/- %.2f"%numpy.array([bsz_mean[0,0,iant,:].std()])),
      lambda iant:(bsz_fmean[0,0,:,iant],bsz_fstd[0,0,:,iant]),
    ];
    make_figure(enumerate(("size","","size fq")),enumerate(ANTS),
          lambda i,iant:funcs[i](iant),
        mean_format="%.4f",
        hline=1,ylock=True,figsize=(290,75),mode=PLOT_ERRORBARS,
        suptitle="Beam extent",
        save="Eshape");

  # make skymap with average pointings
  ll = [];
  mm = [];
  markers = [];

  # add nominal mispointings
  if options.nominals:
    exec(file(options.nominals));
  else:
    nominals = {};

  nominal_radius = options.nominal_circle/(1000/60.);

  circlex1 = numpy.cos(numpy.arange(0,1.05,.05)*math.pi*2);
  circley1 = numpy.sin(numpy.arange(0,1.05,.05)*math.pi*2);
  circlex = circlex1*nominal_radius;
  circley = circley1*nominal_radius;

  ll.append(0);
  mm.append(0);
  markers.append(
    ("plot",(circlex,circley,":"),
        dict(color='blue'))
  );

  for iant,ant in enumerate(ANTS):
    dl = dlm0[0,:,iant,:];
    dm = dlm0[1,:,iant,:];
    dl_mean = dl.mean();
    dl_std = dl.std();
    dm_mean = dm.mean();
    dm_std = dm.std();
    color = "pink" if nominals else "blue";
    # plot nominal position, if available
    if ant in nominals:
      dl0,dm0 = nominals[ant];
      markers += [
        ("text",(dl0/ARCMIN,dm0/ARCMIN,ant),
            dict(color='blue',ha='center',va='center',size='large',weight='bold')),
        ("plot",(dl0/ARCMIN+circlex,dm0/ARCMIN+circley,":"),
            dict(color='blue')),
        ("plot",((dl0/ARCMIN,dl_mean/ARCMIN),(dm0/ARCMIN,dm_mean/ARCMIN),':'),
            dict(color='grey')),
        ];
      ll += [ dl0,dl0,dl0 ];
      mm += [ dm0,dl0,dl0 ];
      color = "red";
    # plot fitted position
    markers += [
      ("errorbar",(dl_mean/ARCMIN,dm_mean/ARCMIN,dm_std/ARCMIN,dl_std/ARCMIN),dict(color='#A0A0A0')),
      ("text",(dl_mean/ARCMIN,dm_mean/ARCMIN,"%s "%ant),
          dict(color=color,ha='center',va='center',size='large',weight='bold',bbox=dict(fc='white',ec='none' if beam_sizes else 'grey'))),
      ];
    ll += [ dl_mean,dl_mean ];
    mm += [ dm_mean,dm_mean ];
    # plot beam size
    if beam_sizes == 1:
      bsz = bsz_mean[0,0,iant,:].mean();
      bsz = min(1.2,max(bsz,.8));
      bsz = bsz - 1;
      radius0 = nominal_radius/4;
      radius = radius0 + bsz*5*radius0;
      markers += [ ("plot",
            ( dl_mean/ARCMIN+circlex1*radius0,
              dm_mean/ARCMIN+circley1*radius0,'-'),
          dict(color='grey',zorder=10,alpha=0.5)) ];
      markers += [ ("plot",
            ( dl_mean/ARCMIN+circlex1*radius,
              dm_mean/ARCMIN+circley1*radius,'-'),
          dict(color='green' if bsz >.01 else 'purple' if bsz <-.01 else 'grey',zorder=20,alpha=0.5)) ];
      ll += [ dl_mean,dl_mean ];
      mm += [ dm_mean,dm_mean ];

  make_skymap(numpy.array(ll),numpy.array(mm),markers,
    zero_lines=False,
    suptitle="Fitted pointing offsets",save="Eplot");


  if options.output_type.upper() == "X11":
    from pylab import plt
    plt.show();
