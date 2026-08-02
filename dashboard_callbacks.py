"""Dash callback and clientside-interaction registration.

The analytical and figure-building implementation remains in ``app.py``.
This module owns callback wiring so update scope, URL persistence, export,
and browser-only interactions can be reviewed without scanning the builders.
``register_callbacks`` receives the app module namespace to preserve the
existing public callback functions and shared in-memory caches.
"""

from __future__ import annotations


def register_callbacks(context):
    """Register callbacks against the supplied dashboard module namespace."""
    class _ContextProxy:
        def __getattr__(self, name):
            return getattr(context["ctx"], name)

    globals().update({
        name: value for name, value in context.items()
        if not name.startswith("__")
    })
    # Preserve direct-call compatibility: tests and scripts historically
    # replace ``app.ctx`` with a small triggered-id stub.
    globals()["ctx"] = _ContextProxy()
    app.clientside_callback(
        "function(v){return {display:(v&&v.indexOf('on')>=0)?'block':'none'};}",
        Output("diag-start-heading-wrap", "style"),
        Input("diag-start-heading-toggle", "value"),
    )
    
    
    app.clientside_callback(
        """
        function(clicks, collapsed) {
          if (!clicks) return window.dash_clientside.no_update;
          return !Boolean(collapsed);
        }
        """,
        Output("sidebar-collapsed-store", "data"),
        Input("btn-toggle-sidebar", "n_clicks"),
        State("sidebar-collapsed-store", "data"),
        prevent_initial_call=True,
    )
    
    
    app.clientside_callback(
        """
        function(collapsed) {
          var hidden = Boolean(collapsed);
          window.setTimeout(function () {
            if (!window.Plotly) return;
            document.querySelectorAll('.js-plotly-plot').forEach(function (gd) {
              try { window.Plotly.Plots.resize(gd); } catch (error) {}
            });
          }, 80);
          return [
            hidden ? 'td-sidebar is-collapsed' : 'td-sidebar',
            hidden ? '›' : '‹',
            hidden ? 'Restore the control sidebar.' :
              'Collapse the control sidebar to give plots the full width.'
          ];
        }
        """,
        Output("sidebar-panel", "className"),
        Output("btn-toggle-sidebar", "children"),
        Output("btn-toggle-sidebar", "title"),
        Input("sidebar-collapsed-store", "data"),
    )
    
    
    app.clientside_callback(
        "function(mode){return mode==='compare'?"
        "'plot-drop-target plot-workspace compare-workspace':"
        "'plot-drop-target plot-workspace';}",
        Output("plot-drop-target", "className"),
        Input("view-layout", "value"),
    )
    
    
    app.clientside_callback(
        """
        function(addClicks, deleteClicks, active, x0, x1, z0, z1, regions) {
          var no = window.dash_clientside.no_update;
          var cc = window.dash_clientside.callback_context || {};
          var trigger = cc.triggered_id;
          var clean = Array.isArray(regions) ?
            JSON.parse(JSON.stringify(regions)) : [];
          if (!clean.length) clean = [{
            id:'region-1', name:'Window 1', x0:-3, x1:3, z0:-3, z1:3
          }];
          function options() {
            return clean.map(function(r, i) {
              return {label:String(r.name || ('Window '+(i+1))), value:String(r.id)};
            });
          }
          function selected(id) {
            return clean.filter(function(r){return String(r.id)===String(id);})[0] || clean[0];
          }
          function values(r) {
            return [r.x0, r.x1, r.z0, r.z1].map(Number);
          }
          if (trigger === 'btn-custom-region-add') {
            var suffix=1, used={};
            clean.forEach(function(r){used[String(r.id)]=true;});
            while (used['region-'+suffix]) suffix += 1;
            var base=selected(active);
            var width=Math.max(0.001,Number(base.x1)-Number(base.x0));
            var height=Math.max(0.001,Number(base.z1)-Number(base.z0));
            var next={
              id:'region-'+suffix,name:'Window '+suffix,
              x0:Number(base.x0)+width*0.25,x1:Number(base.x1)+width*0.25,
              z0:Number(base.z0)+height*0.25,z1:Number(base.z1)+height*0.25
            };
            clean.push(next);
            return [clean,options(),next.id,next.x0,next.x1,next.z0,next.z1];
          }
          if (trigger === 'btn-custom-region-delete') {
            if (clean.length <= 1) return [no,no,no,no,no,no,no];
            var oldIndex=clean.findIndex(function(r){return String(r.id)===String(active);});
            clean=clean.filter(function(r){return String(r.id)!==String(active);});
            var after=clean[Math.max(0,Math.min(clean.length-1,oldIndex))];
            var av=values(after);
            return [clean,options(),after.id,av[0],av[1],av[2],av[3]];
          }
          if (trigger === 'custom-region-active' || !active ||
              trigger === 'custom-regions-store') {
            var chosen=selected(active), cv=values(chosen);
            return [no,options(),chosen.id,cv[0],cv[1],cv[2],cv[3]];
          }
          if (['custom-region-x0','custom-region-x1',
               'custom-region-z0','custom-region-z1'].indexOf(trigger) >= 0) {
            var nums=[Number(x0),Number(x1),Number(z0),Number(z1)];
            if (!nums.every(Number.isFinite) || nums[1]<=nums[0] || nums[3]<=nums[2]) {
              return [no,no,no,no,no,no,no];
            }
            var changed=false;
            clean=clean.map(function(r){
              if(String(r.id)!==String(active))return r;
              if(Number(r.x0)!==nums[0]||Number(r.x1)!==nums[1]||
                 Number(r.z0)!==nums[2]||Number(r.z1)!==nums[3]){
                changed=true;
                return Object.assign({},r,{x0:nums[0],x1:nums[1],
                  z0:nums[2],z1:nums[3]});
              }
              return r;
            });
            return [changed?clean:no,no,no,no,no,no,no];
          }
          var initial=selected(active), iv=values(initial);
          return [no,options(),initial.id,iv[0],iv[1],iv[2],iv[3]];
        }
        """,
        Output("custom-regions-store", "data"),
        Output("custom-region-active", "options"),
        Output("custom-region-active", "value"),
        Output("custom-region-x0", "value", allow_duplicate=True),
        Output("custom-region-x1", "value", allow_duplicate=True),
        Output("custom-region-z0", "value", allow_duplicate=True),
        Output("custom-region-z1", "value", allow_duplicate=True),
        Input("btn-custom-region-add", "n_clicks"),
        Input("btn-custom-region-delete", "n_clicks"),
        Input("custom-region-active", "value"),
        Input("custom-region-x0", "value"),
        Input("custom-region-x1", "value"),
        Input("custom-region-z0", "value"),
        Input("custom-region-z1", "value"),
        State("custom-regions-store", "data"),
        prevent_initial_call="initial_duplicate",
    )
    
    
    app.clientside_callback(
        """
        function(enabled, regions, active, stats, style, traj, heat, flow) {
          if (window.dash_clientside.region_observer) {
            return window.dash_clientside.region_observer.render(
              enabled, regions, active, stats, style
            );
          }
          return 'Loading observation windows…';
        }
        """,
        Output("custom-region-status", "children"),
        Input("custom-region-enabled", "value"),
        Input("custom-regions-store", "data"),
        Input("custom-region-active", "value"),
        Input("custom-region-stats-store", "data"),
        Input("visual-style-store", "data"),
        Input("trajectory-plot", "figure"),
        Input("heatmap-figure-store", "data"),
        Input("flow-figure-store", "data"),
    )
    
    
    app.clientside_callback(
        """
        function(enabled, regions) {
          var active = Array.isArray(enabled) && enabled.indexOf('on') >= 0;
          if (window.__tdRegionAnalysisTimer) {
            window.clearTimeout(window.__tdRegionAnalysisTimer);
            window.__tdRegionAnalysisTimer = null;
          }
          var signature = JSON.stringify(regions || []);
          if (!active) {
            window.setTimeout(function() {
              if (window.dash_clientside && window.dash_clientside.set_props) {
                window.dash_clientside.set_props(
                  'custom-region-analysis-request',
                  {data:{
                    signature:signature,
                    requested:Date.now(),
                    reason:'disabled'
                  }}
                );
              }
            }, 0);
            return {pending:false, signature:signature, updated:Date.now()};
          }
          window.__tdRegionAnalysisTimer = window.setTimeout(function() {
            window.__tdRegionAnalysisTimer = null;
            if (window.dash_clientside && window.dash_clientside.set_props) {
              window.dash_clientside.set_props(
                'custom-region-analysis-request',
                {data:{
                  signature:signature,
                  requested:Date.now(),
                  reason:'geometry-idle'
                }}
              );
            }
          }, 7000);
          return {
            pending:true, signature:signature, requested:Date.now(),
            delay_ms:7000
          };
        }
        """,
        Output("custom-region-debounce-state", "data"),
        Input("custom-region-enabled", "value"),
        Input("custom-regions-store", "data"),
        prevent_initial_call=True,
    )
    
    
    app.clientside_callback(
        """
        function(value, regionFigure, targetFigure) {
          var visible = Array.isArray(value) && value.indexOf('on') >= 0;
          ['custom-region-diagnostics-plot', 'roi-plot'].forEach(function(id) {
            var host = document.getElementById(id);
            var gd = host && host.querySelector('.js-plotly-plot');
            if (!gd || !window.Plotly) return;
            var indices = [];
            (gd.data || []).forEach(function(trace, index) {
              if (trace && trace.meta && trace.meta.td_pairing) indices.push(index);
            });
            if (indices.length) {
              window.Plotly.restyle(gd, {visible: visible}, indices);
            }
          });
          return visible ? 'paired lines on' : 'paired lines off';
        }
        """,
        Output("anim-dummy", "children", allow_duplicate=True),
        Input("observation-paired-lines", "value"),
        Input("custom-region-diagnostics-plot", "figure"),
        Input("roi-plot", "figure"),
        prevent_initial_call="initial_duplicate",
    )
    
    
    app.clientside_callback(
        """
        function(clicks, current) {
          if (!clicks) return window.dash_clientside.no_update;
          return !Boolean(current);
        }
        """,
        Output("minimal-layout-store", "data"),
        Input("btn-minimal-layout", "n_clicks"),
        State("minimal-layout-store", "data"),
        prevent_initial_call=True,
    )
    
    
    app.clientside_callback(
        "function(on){return on?'Full layout':'Clean layout';}",
        Output("btn-minimal-layout", "children"),
        Input("minimal-layout-store", "data"),
    )
    
    
    app.clientside_callback(
        """
        function(on, unitScale, unitLabel) {
          if (window.dash_clientside.clean_layout) {
            return window.dash_clientside.clean_layout.render(
              on, unitScale, unitLabel
            );
          }
          return on ? 'Clean layout is loading.' :
            'Hide spatial axes and Cartesian grids without changing plot data.';
        }
        """,
        Output("btn-minimal-layout", "title"),
        Input("minimal-layout-store", "data"),
        Input("spatial-unit-scale", "value"),
        Input("spatial-unit-label", "value"),
    )
    
    
    app.clientside_callback(
        "function(enabled){return {display:"
        "(enabled&&enabled.indexOf('on')>=0)?'block':'none'};}",
        Output("loop-observer-wrap", "style"),
        Input("loop-enabled", "value"),
    )


    app.clientside_callback(
        "function(enabled){return {display:"
        "(enabled&&enabled.indexOf('on')>=0)?'block':'none'};}",
        Output("view-heading", "style"),
        Input("heading-time-enabled", "value"),
    )


    app.clientside_callback(
        "function(exact,slider){"
        "var no=window.dash_clientside.no_update;"
        "var cc=window.dash_clientside.callback_context||{};"
        "var trigger=String(cc.triggered_id||'');"
        "if(trigger==='heading-time-window-slider'){"
        "var value=Number(slider);return [value<0?null:value,no];}"
        "if(trigger==='heading-time-window'){"
        "if(exact===null||exact===''||!Number.isFinite(Number(exact)))return [no,-1];"
        "return [no,Math.max(0,Math.min(30,Number(exact)))];}"
        "return [no,no];}",
        Output("heading-time-window", "value", allow_duplicate=True),
        Output("heading-time-window-slider", "value", allow_duplicate=True),
        Input("heading-time-window", "value"),
        Input("heading-time-window-slider", "value"),
        prevent_initial_call="initial_duplicate",
    )


    app.clientside_callback(
        "function(enabled,mode,representation,windowValue,variability,angleBin,state,polar){"
        "if(!(enabled&&enabled.indexOf('on')>=0))return 'off';"
        "var wanted=mode==='animal'?'animal':'trial';"
        "var view=representation==='density'?'density':'traces';"
        "var requested=(windowValue===null||windowValue==='')?'auto':"
        "(Number(windowValue)===0?'full':String(Number(windowValue)));"
        "var wantsBand=!!(variability&&variability.indexOf('on')>=0);"
        "var current=state&&state.enabled&&state.mode===wanted&&"
        "state.representation===view&&String(state.requested_window)===requested&&"
        "Boolean(state.variability)===wantsBand&&"
        "Number(state.angle_bin_degrees)===Number(angleBin)&&state.seconds!=null&&"
        "(!polar||!polar.epoch||"
        "Number(state.epoch)>=Number(polar.epoch));"
        "if(current)return (view==='density'?'density layers':"
        "(wanted==='animal'?'animal mean':'trial traces'))+"
        "' ready · '+String(state.window_label||'')+' · '+"
        "Number(state.seconds).toFixed(2)+' s';"
        "return 'updating · previous plot remains visible';}",
        Output("heading-time-status", "children", allow_duplicate=True),
        Input("heading-time-enabled", "value"),
        Input("heading-time-mode", "value"),
        Input("heading-time-representation", "value"),
        Input("heading-time-window", "value"),
        Input("heading-time-variability", "value"),
        Input("heading-time-angle-bin", "value"),
        Input("heading-time-render-state", "data"),
        Input("polar-render-state", "data"),
        prevent_initial_call="initial_duplicate",
    )


    app.clientside_callback(
        """
        function(gandiva, transition) {
          function style(value) {
            var on=Array.isArray(value) && value.indexOf('on')>=0;
            return {display:on?'block':'none', position:'relative',
                    overflow:'visible', scrollMarginTop:'52px',
                    marginBottom:'12px'};
          }
          return [style(gandiva), style(transition)];
        }
        """,
        Output("view-flow", "style"),
        Output("view-transition", "style"),
        Input("gandiva-enabled", "value"),
        Input("transition-enabled", "value"),
    )
    
    
    app.clientside_callback(
        """
        function(value, trajectory, heatmap, flow, polar) {
          if (window.dash_clientside.target_visibility) {
            return window.dash_clientside.target_visibility.render(value);
          }
          return '';
        }
        """,
        Output("anim-dummy", "children", allow_duplicate=True),
        Input("roi-show", "value"),
        Input("trajectory-plot", "figure"),
        Input("heatmap-figure-store", "data"),
        Input("flow-figure-store", "data"),
        Input("polar-plot", "figure"),
        prevent_initial_call="initial_duplicate",
    )
    
    
    app.clientside_callback(
        """
        function(bundle, outcome, metric, enabled, countMin, countMax) {
          if (window.TransitionProbabilityObserver) {
            return window.TransitionProbabilityObserver.renderDashboard({
              bundle: bundle || {},
              outcome: outcome || 'crossed',
              metric: metric || 'fraction',
              countMin: countMin,
              countMax: countMax,
              enabled: enabled
            });
          }
          return 'Loading transition observer…';
        }
        """,
        Output("transition-status", "children"),
        Input("transition-data-store", "data"),
        Input("transition-outcome", "value"),
        Input("transition-metric", "value"),
        Input("transition-enabled", "value"),
        State("transition-count-min", "value"),
        State("transition-count-max", "value"),
    )
    
    
    app.clientside_callback(
        """
        function(countMin, countMax) {
          if (window.TransitionProbabilityObserver) {
            return window.TransitionProbabilityObserver.setDashboardCountRange(
              countMin, countMax
            );
          }
          return window.dash_clientside.no_update;
        }
        """,
        Output("anim-dummy", "children", allow_duplicate=True),
        Input("transition-count-min", "value"),
        Input("transition-count-max", "value"),
        prevent_initial_call=True,
    )
    
    
    app.clientside_callback(
        "function(fig,enabled,rings,active,matchMode,style,fraction,seed){"
        "if(window.TrajectoryTrialSubset){"
        "fig=window.TrajectoryTrialSubset.filterFigure(fig,fraction,seed);}"
        "if(window.dash_clientside.loop_observer){"
        "return window.dash_clientside.loop_observer.render("
        "fig,enabled,rings,active,matchMode,style);}"
        "return 'Loading loop observer…';}",
        Output("loop-observer-status", "children"),
        Input("trajectory-plot", "figure"),
        Input("loop-enabled", "value"),
        Input("loop-rings-store", "data"),
        Input("loop-active-ring", "value"),
        Input("loop-match-mode", "value"),
        Input("visual-style-store", "data"),
        Input("traj-trial-fraction", "value"),
        Input("btn-traj-resample", "n_clicks"),
    )
    
    
    app.clientside_callback(
        """
        function(addClicks, deleteClicks, active, x, z, radius, rings) {
          var no = window.dash_clientside.no_update;
          var cc = window.dash_clientside.callback_context || {};
          var trigger = cc.triggered_id;
          var clean = Array.isArray(rings) ? JSON.parse(JSON.stringify(rings)) : [];
          if (!clean.length) {
            clean = [{id:'ring-1', name:'Ring 1', x:0, z:0, radius:3}];
          }
          function options() {
            return clean.map(function(r, i) {
              return {label:String(r.name || ('Ring '+(i+1))), value:String(r.id)};
            });
          }
          function selected(id) {
            return clean.filter(function(r){return String(r.id)===String(id);})[0] || clean[0];
          }
          if (trigger === 'btn-loop-add') {
            var suffix = 1;
            var used = {};
            clean.forEach(function(r){used[String(r.id)] = true;});
            while (used['ring-'+suffix]) suffix += 1;
            var base = selected(active);
            var next = {
              id:'ring-'+suffix, name:'Ring '+suffix,
              x:Number(base.x||0)+Number(base.radius||3)*0.55,
              z:Number(base.z||0)+Number(base.radius||3)*0.55,
              radius:Math.max(0.001, Number(base.radius||3))
            };
            clean.push(next);
            return [clean, options(), next.id, next.x, next.z, next.radius];
          }
          if (trigger === 'btn-loop-delete') {
            if (clean.length <= 1) return [no,no,no,no,no,no];
            var oldIndex = clean.findIndex(function(r){return String(r.id)===String(active);});
            clean = clean.filter(function(r){return String(r.id)!==String(active);});
            var nextIndex = Math.max(0, Math.min(clean.length-1, oldIndex));
            var after = clean[nextIndex];
            return [clean, options(), after.id, after.x, after.z, after.radius];
          }
          if (trigger === 'loop-active-ring' || !active) {
            var chosen = selected(active);
            return [no, options(), chosen.id, chosen.x, chosen.z, chosen.radius];
          }
          if (trigger === 'loop-x' || trigger === 'loop-z' ||
              trigger === 'loop-radius') {
            var changed = false;
            clean = clean.map(function(r) {
              if (String(r.id)!==String(active)) return r;
              var nx=Number(x), nz=Number(z), nr=Number(radius);
              if (!Number.isFinite(nx) || !Number.isFinite(nz) ||
                  !Number.isFinite(nr) || nr<=0) return r;
              if (Number(r.x)!==nx || Number(r.z)!==nz || Number(r.radius)!==nr) {
                changed = true;
                return Object.assign({}, r, {x:nx,z:nz,radius:nr});
              }
              return r;
            });
            return [changed ? clean : no, no, no, no, no, no];
          }
          var initial = selected(active);
          return [no, options(), initial.id, initial.x, initial.z, initial.radius];
        }
        """,
        Output("loop-rings-store", "data"),
        Output("loop-active-ring", "options"),
        Output("loop-active-ring", "value"),
        Output("loop-x", "value", allow_duplicate=True),
        Output("loop-z", "value", allow_duplicate=True),
        Output("loop-radius", "value", allow_duplicate=True),
        Input("btn-loop-add", "n_clicks"),
        Input("btn-loop-delete", "n_clicks"),
        Input("loop-active-ring", "value"),
        Input("loop-x", "value"),
        Input("loop-z", "value"),
        Input("loop-radius", "value"),
        State("loop-rings-store", "data"),
        prevent_initial_call="initial_duplicate",
    )
    
    
    app.clientside_callback(
        "function(exact,slider){"
        "var no=window.dash_clientside.no_update;"
        "var cc=window.dash_clientside.callback_context||{};"
        "var trigger=cc.triggered_id;"
        "if(trigger==='loop-radius-slider')return [slider,no];"
        "if(trigger==='loop-radius'){"
        "var value=Number(exact);"
        "if(!isFinite(value)||value<=0)return [no,no];"
        "return [no,Math.max(0.5,Math.min(100,value))];}"
        "return [no,no];}",
        Output("loop-radius", "value", allow_duplicate=True),
        Output("loop-radius-slider", "value"),
        Input("loop-radius", "value"),
        Input("loop-radius-slider", "value"),
        prevent_initial_call=True,
    )
    
    
    app.clientside_callback(
        """
        function(trajectory, polar, heading, fraction, seed, summary) {
          if (window.dash_clientside.trial_subset) {
            var state = window.dash_clientside.trial_subset.render(
              trajectory, polar, heading, fraction, seed
            );
            var text = (typeof summary === 'string') ? summary.replace(
              /[\\d,.]+[KMB]?\\/[\\d,.]+[KMB]? displayed trials/i,
              Number(state.selected).toLocaleString() + '/' +
                Number(state.total).toLocaleString() +
                ' displayed trials'
            ) : window.dash_clientside.no_update;
            return [state, text];
          }
          return [window.dash_clientside.no_update,
                  window.dash_clientside.no_update];
        }
        """,
        Output("trial-subset-state", "data"),
        Output("data-summary", "children", allow_duplicate=True),
        Input("trajectory-plot", "figure"),
        Input("polar-plot", "figure"),
        Input("heading-time-plot", "figure"),
        Input("traj-trial-fraction", "value"),
        Input("btn-traj-resample", "n_clicks"),
        State("data-summary", "children"),
        prevent_initial_call=True,
    )
    
    
    # Keep a compact, always-visible account of the latest instrumented activity.
    # The progress snapshot, rather than Dash's page-global loading class, drives
    # the phase dot so URL persistence and other tiny callbacks do not flash it.
    app.clientside_callback(
        "function(load,plot,summary,render,polar,generation,progress){"
        "progress=progress||{};"
        "var loaded=generation&&Number(generation.loaded||0);"
        "var completed=render&&Number(render.completed||0);"
        "var pending=loaded&&(!completed||loaded>completed);"
        "var loadIssue=load&&(/^(No |Choose|Could not|Failed|Error)/i).test(load);"
        "var message=progress.active?(progress.message||progress.phase||'Working…'):"
        "(pending?(load||'Loading the selected dataset…'):"
        "(loadIssue?load:(plot||summary||load||'Choose a data source to begin.')));"
        "if(!progress.active&&progress.message&&progress.kind!=='idle')message=progress.message;"
        "var loadBusy=/^(Started|Loading|Processing)/i.test(String(load||''));"
        "var plotReady=/^Ready/i.test(String(plot||''));"
        "if(!progress.active&&loadBusy)message=load;"
        "else if(!progress.active&&plotReady&&summary)message=plot;"
        "var bits=[];if(load)bits.push(load);"
        "if(summary&&summary!==message)bits.push(summary);"
        "var done=render&&render.completed;if(done){"
        "try{bits.push('Last render '+new Date(done*1000).toLocaleTimeString());}catch(e){}}"
        "if(generation&&generation.pattern&&!load)bits.push(generation.pattern);"
        "bits.push('Errors and tracebacks: server terminal');"
        "var op=render||{};if(polar&&Number(polar.completed||0)>Number(op.completed||0))op=polar;"
        "var tip=[];if(progress.kind)tip.push('Operation: '+progress.kind);"
        "var stages=progress.stages||[];stages.forEach(function(s){"
        "var mark=s.status==='done'?'✓':(s.status==='active'?'▶':(s.status==='error'?'✕':'○'));"
        "var timing=(s.seconds===null||s.seconds===undefined)?'':(' · '+Number(s.seconds).toFixed(3)+' s');"
        "var fraction=(s.total>1)?(' · '+Number(s.done||0)+'/'+Number(s.total)) : '';"
        "tip.push(mark+' '+s.label+fraction+timing);});"
        "if(op.operation)tip.push('Last render: '+op.operation);"
        "var tm=op.timings||{};Object.keys(tm).forEach(function(k){"
        "var v=Number(tm[k]);if(isFinite(v))tip.push(k+': '+v.toFixed(3)+' s');});"
        "if(!tip.length)tip.push('Timing appears after the first completed render.');"
        "tip.push('Full errors and tracebacks are in the server terminal.');"
        "var looksBusy=/^(Started|Rendering|Applying|Queued|Updating|Building|Loading)/i.test(String(message||''));"
        "var phase=(progress.active||looksBusy)?(progress.phase||'Working'):"
        "(progress.phase==='Error'?'Error':'Ready');"
        "return [message,bits.join(' • '),tip.join('\\n'),phase];}",
        Output("status-message", "children"),
        Output("status-detail", "children"),
        Output("status-dock", "title"),
        Output("status-phase", "children"),
        Input("load-status", "children"),
        Input("plot-status", "children"),
        Input("data-summary", "children"),
        Input("spatial-render-state", "data"),
        Input("polar-render-state", "data"),
        Input("data-generation", "data"),
        Input("operation-progress", "data"),
    )
    
    app.clientside_callback(
        """
        function(progress, plot, load) {
          progress = progress || {};
          var classes = 'status-dock header-status';
          var message = String(plot || load || '');
          var busy = /^(Started|Rendering|Applying|Queued|Updating|Building|Loading)/i.test(message);
          if (progress.active || busy) return classes + ' is-working';
          if (progress.phase === 'Error') return classes + ' is-error';
          return classes;
        }
        """,
        Output("status-dock", "className"),
        Input("operation-progress", "data"),
        Input("plot-status", "children"),
        Input("load-status", "children"),
    )
    
    app.clientside_callback(
        "function(n,pattern,armed){if(!n||!pattern)return window.dash_clientside.no_update;"
        "var labels={'trial-range':'trial subset','trial-min':'trial subset',"
        "'trial-max':'trial subset','step-range':'step subset','step-min':'step subset',"
        "'step-max':'step subset','vel-range':'velocity subset',"
        "'disp-range':'displacement subset','walk-range':'distance-walked subset',"
        "'filter-configs':'config subset',"
        "'filter-vrs':'VR subset','filter-flyids':'animal subset',"
        "'filter-scenes':'scene subset','filter-folders':'folder subset'};"
        "var fresh=armed&&((Date.now()/1000-Number(armed.ts||0))<4)&&"
        "Number(n)===Number(armed.clicks||0)+1;"
        "if(fresh){var key=String(armed.trigger||'filters');"
        "return 'Applying '+(labels[key]||key.replace(/-/g,' '))+' and rebuilding sections…';}"
        "return 'Rendering all sections… request '+n;}",
        Output("plot-status", "children", allow_duplicate=True),
        Input("btn-plot", "n_clicks"),
        State("store-glob", "data"),
        State("auto-replot-state", "data"),
        prevent_initial_call=True,
    )
    
    app.clientside_callback(
        "function(n,pattern){if(!n)return window.dash_clientside.no_update;"
        "if(!pattern)return 'Choose a data source before loading.';"
        "return 'Started — processing in background threads; plots and delayed "
        "statistics are staged separately.';}",
        Output("load-status", "children", allow_duplicate=True),
        Input("btn-load", "n_clicks"),
        State("glob-input", "value"),
        prevent_initial_call=True,
    )
    
    app.clientside_callback(
        "function(n,pattern){if(!n)return window.dash_clientside.no_update;"
        "if(!pattern)return 'Load data before exporting.';"
        "return 'Building self-contained HTML export…';}",
        Output("plot-status", "children", allow_duplicate=True),
        Input("btn-export", "n_clicks"),
        State("store-glob", "data"),
        prevent_initial_call=True,
    )
    
    # The section tabs are navigation, not conditional rendering. Every graph stays
    # mounted; changing the tab only moves the existing main scroller to that card.
    app.clientside_callback(
        "function(view){if(window.__scrollTrajectorySection){"
        "window.__scrollTrajectorySection(view,'smooth');return '';}"
        "var scroller=document.getElementById('main-scroll');"
        "var target=document.getElementById('view-'+view);"
        "if(scroller&&target)scroller.scrollTo({top:target.offsetTop,behavior:'smooth'});"
        "return '';}",
        Output("anim-dummy", "children", allow_duplicate=True),
        Input("view-mode", "value"),
        prevent_initial_call=True,
    )
    
    
    # ---------------------------------------------------------------------------
    # Callbacks
    # ---------------------------------------------------------------------------
    
    # Full URL <-> state. Keep these keys in sync with update_url().
    _URL_NUM = {"vel": "vel-threshold", "disp": "min-disp", "trim": "trim-samples",
                "jb": "jump-buffer", "hbin": "heatmap-binsize", "hbound": "heatmap-bound",
                "hcmin": "heatmap-cmin", "hcmax": "heatmap-cmax", "ncols": "subplot-ncols",
                "pts": "plot-points", "tmin": "trial-min", "tmax": "trial-max",
                "smin": "step-min", "smax": "step-max",
                "reach": "roi-reach", "frad": "flow-max-radius",
                "tf": "traj-trial-fraction",
                "lx": "loop-x", "lz": "loop-z", "lr": "loop-radius",
                "rmin": "polar-r-range", "rmax": "polar-r-range",
                "vrmin": "vel-range", "vrmax": "vel-range",
                "drmin": "disp-range", "drmax": "disp-range",
                "wrmin": "walk-range", "wrmax": "walk-range",
                "htwin": "heading-time-window", "htbin": "heading-time-angle-bin",
                "pmin": "polar-min-point-frac", "amin": "polar-min-animal-frac",
                "uscale": "spatial-unit-scale",
                "tsplit": "transition-split-z",
                "trnmin": "transition-min-trials",
                "tcmin": "transition-count-min",
                "tcmax": "transition-count-max"}
    _URL_STR = {"groupby": "group-by", "pool": "pool-mode", "color": "color-by",
                "hscale": "heatmap-scale", "hmetric": "heatmap-metric",
                "hcrange": "heatmap-crange", "pang": "polar-angle-source",
                "htmode": "heading-time-mode",
                "htview": "heading-time-representation",
                "layout": "view-layout", "ractive": "custom-region-active",
                "dist": "distribution-mode", "sunit": "stats-unit",
                "ulabel": "spatial-unit-label",
                "tmode": "transition-outcome",
                "tmetric": "transition-metric"}
    _URL_LIST = {"fcfg": "filter-configs", "fvr": "filter-vrs", "ffly": "filter-flyids",
                 "fscn": "filter-scenes", "ffld": "filter-folders", "raw": "raw-columns"}
    
    
    @app.callback(
        Output("glob-input", "value"),
        Output("vel-threshold", "value", allow_duplicate=True),
        Output("min-disp", "value", allow_duplicate=True),
        Output("trim-samples", "value", allow_duplicate=True),
        Output("jump-buffer", "value", allow_duplicate=True),
        Output("group-by", "value", allow_duplicate=True),
        Output("pool-mode", "value", allow_duplicate=True),
        Output("color-by", "value", allow_duplicate=True),
        Output("animate-toggle", "value", allow_duplicate=True),
        Output("rebase-origin", "value", allow_duplicate=True),
        Output("heatmap-binsize", "value", allow_duplicate=True),
        Output("heatmap-scale", "value", allow_duplicate=True),
        Output("heatmap-bound", "value", allow_duplicate=True),
        Output("heatmap-metric", "value", allow_duplicate=True),
        Output("heatmap-cmin", "value", allow_duplicate=True),
        Output("heatmap-cmax", "value", allow_duplicate=True),
        Output("heatmap-crange", "value", allow_duplicate=True),
        Output("filter-configs", "value", allow_duplicate=True),
        Output("filter-vrs", "value", allow_duplicate=True),
        Output("filter-flyids", "value", allow_duplicate=True),
        Output("filter-scenes", "value", allow_duplicate=True),
        Output("filter-folders", "value", allow_duplicate=True),
        Output("trial-min", "value", allow_duplicate=True),
        Output("trial-max", "value", allow_duplicate=True),
        Output("step-min", "value", allow_duplicate=True),
        Output("step-max", "value", allow_duplicate=True),
        Output("raw-columns", "value", allow_duplicate=True),
        Output("subplot-ncols", "value", allow_duplicate=True),
        Output("plot-points", "value", allow_duplicate=True),
        Output("polar-r-range", "value", allow_duplicate=True),
        Output("vel-range", "value", allow_duplicate=True),
        Output("vel-range-min", "value", allow_duplicate=True),
        Output("vel-range-max", "value", allow_duplicate=True),
        Output("disp-range", "value", allow_duplicate=True),
        Output("walk-range", "value", allow_duplicate=True),
        Output("heatmap-color-range", "value", allow_duplicate=True),
        Output("trial-range", "value", allow_duplicate=True),
        Output("step-range", "value", allow_duplicate=True),
        Output("polar-min-point-frac", "value", allow_duplicate=True),
        Output("polar-min-animal-frac", "value", allow_duplicate=True),
        Output("polar-angle-source", "value", allow_duplicate=True),
        Output("heading-time-enabled", "value", allow_duplicate=True),
        Output("heading-time-mode", "value", allow_duplicate=True),
        Output("heading-time-representation", "value", allow_duplicate=True),
        Output("heading-time-window", "value", allow_duplicate=True),
        Output("heading-time-variability", "value", allow_duplicate=True),
        Output("heading-time-angle-bin", "value", allow_duplicate=True),
        Output("render-mode", "value", allow_duplicate=True),
        Output("view-mode", "value", allow_duplicate=True),
        Output("view-layout", "value", allow_duplicate=True),
        Output("viewport-store", "data", allow_duplicate=True),
        Output("flow-max-radius", "value", allow_duplicate=True),
        Output("roi-reach", "value", allow_duplicate=True),
        Output("traj-trial-fraction", "value", allow_duplicate=True),
        Output("loop-enabled", "value", allow_duplicate=True),
        Output("loop-x", "value", allow_duplicate=True),
        Output("loop-z", "value", allow_duplicate=True),
        Output("loop-radius", "value", allow_duplicate=True),
        Output("loop-rings-store", "data", allow_duplicate=True),
        Output("loop-active-ring", "value", allow_duplicate=True),
        Output("loop-match-mode", "value", allow_duplicate=True),
        Output("custom-region-enabled", "value", allow_duplicate=True),
        Output("custom-regions-store", "data", allow_duplicate=True),
        Output("custom-region-active", "value", allow_duplicate=True),
        Output("distribution-mode", "value", allow_duplicate=True),
        Output("distribution-show-points", "value", allow_duplicate=True),
        Output("stats-unit", "value", allow_duplicate=True),
        Output("observation-paired-lines", "value", allow_duplicate=True),
        Output("spatial-unit-scale", "value", allow_duplicate=True),
        Output("spatial-unit-label", "value", allow_duplicate=True),
        Output("transition-enabled", "value", allow_duplicate=True),
        Output("transition-outcome", "value", allow_duplicate=True),
        Output("transition-metric", "value", allow_duplicate=True),
        Output("transition-count-min", "value", allow_duplicate=True),
        Output("transition-count-max", "value", allow_duplicate=True),
        Output("transition-split-z", "value", allow_duplicate=True),
        Output("transition-min-trials", "value", allow_duplicate=True),
        Output("minimal-layout-store", "data", allow_duplicate=True),
        Output("url-restored", "data"),
        Input("url", "search"),
        State("url-restored", "data"),
        prevent_initial_call="initial_duplicate",
    )
    def restore_from_url(search, already):
        # All outputs except the final url-restored flag. The guarded early-return
        # appends that flag below, so this count must remain one below total arity.
        n_out = 78
        # Restore exactly once (the first time the URL is seen). Later URL writes
        # come from update_url echoing current state — ignore them to avoid a loop.
        if already:
            return (no_update,) * n_out + (no_update,)
        if not search:
            return (no_update,) * n_out + (True,)
        p = parse_qs(search.lstrip("?"))
    
        def num(k):
            if k not in p:
                return no_update
            try:
                v = float(p[k][0]); return int(v) if v.is_integer() else v
            except Exception:
                return no_update
    
        def positive_num(k):
            value = num(k)
            if value is no_update:
                return no_update
            numeric = float(value)
            return value if np.isfinite(numeric) and numeric > 0 else no_update
    
        def finite_num(k):
            value = num(k)
            if value is no_update:
                return no_update
            return value if np.isfinite(float(value)) else no_update
    
        def display_percent():
            value = finite_num("tf")
            if value is no_update:
                return no_update
            return min(100.0, max(1.0, float(value)))
    
        def jump_ms():
            if "jb" not in p:
                return no_update
            try:
                v = float(p["jb"][0])
                # Historical URLs stored seconds (0.1). The control now shows ms.
                out = v * 1000 if v <= 10 else v
                return int(out) if float(out).is_integer() else out
            except Exception:
                return no_update
    
        def s(k):
            return p[k][0] if k in p else no_update
    
        def lst(k):
            return p[k][0].split(",") if (k in p and p[k][0]) else no_update
    
        def r_range():
            if "rmin" not in p and "rmax" not in p:
                return no_update
            lo = num("rmin")
            hi = num("rmax")
            if lo is no_update:
                lo = 0
            if hi is no_update:
                hi = 1
            lo, hi = _polar_r_range([lo, hi])
            return [lo, hi]
    
        def range_pair(lo_key, hi_key, default_lo=None, default_hi=None):
            if lo_key not in p and hi_key not in p:
                return no_update
            lo = num(lo_key)
            hi = num(hi_key)
            if lo is no_update:
                lo = default_lo
            if hi is no_update:
                hi = default_hi
            if lo is None or hi is None:
                return no_update
            rng = _numeric_range([lo, hi])
            return list(rng) if rng else no_update
    
        def trial_slider_range():
            if "tmin" not in p or "tmax" not in p:
                return no_update
            return range_pair("tmin", "tmax")
    
        def step_slider_range():
            if "smin" not in p or "smax" not in p:
                return no_update
            return range_pair("smin", "smax")
    
        def heat_color_slider_range():
            rng = range_pair("hcmin", "hcmax", 0, 100)
            if rng is no_update:
                return no_update
            mode = p.get("hcrange", ["percentile"])[0]
            if mode == "percentile":
                return rng if 0 <= rng[0] <= 100 and 0 <= rng[1] <= 100 else no_update
            return rng
    
        anim = (["on"] if p["anim"][0] == "1" else []) if "anim" in p else no_update
        loop_enabled = (
            ["on"] if p["loop"][0] == "1" else []
        ) if "loop" in p else no_update
        custom_region_enabled = (
            ["on"] if p["region"][0] == "1" else []
        ) if "region" in p else no_update
        distribution_mode = (
            p["dist"][0]
            if p.get("dist", [""])[0] in ("auto", "swarm", "violin")
            else no_update
        )
        distribution_points = (
            ["on"] if p["dpts"][0] == "1" else []
        ) if "dpts" in p else no_update
        stats_unit = (
            p["sunit"][0]
            if p.get("sunit", [""])[0] in ("trial", "animal")
            else no_update
        )
        paired_lines = (
            ["on"] if p["pairs"][0] == "1" else []
        ) if "pairs" in p else no_update
        transition_enabled = (
            ["on"] if p["trans"][0] == "1" else []
        ) if "trans" in p else no_update
        heading_time_enabled = (
            ["on"] if p["heading"][0] == "1" else []
        ) if "heading" in p else no_update
        heading_time_mode = (
            p["htmode"][0]
            if p.get("htmode", [""])[0] in ("trial", "animal")
            else no_update
        )
        heading_time_representation = (
            p["htview"][0]
            if p.get("htview", [""])[0] in ("traces", "density")
            else no_update
        )
        heading_time_window = finite_num("htwin")
        if (heading_time_window is not no_update
                and float(heading_time_window) < 0):
            heading_time_window = no_update
        heading_time_variability = (
            ["on"] if p["htvar"][0] == "1" else []
        ) if "htvar" in p else no_update
        heading_time_angle_bin = positive_num("htbin")
        if (heading_time_angle_bin is not no_update
                and float(heading_time_angle_bin) > 90):
            heading_time_angle_bin = no_update
        transition_outcome = (
            p["tmode"][0]
            if p.get("tmode", [""])[0] in TRANSITION_OUTCOMES
            else no_update
        )
        transition_metric = (
            p["tmetric"][0]
            if p.get("tmetric", [""])[0] in TRANSITION_METRICS
            else no_update
        )
        minimal_layout = (
            p["minimal"][0] == "1"
        ) if "minimal" in p else no_update
        rebase = []
        view = (
            p["view"][0]
            if p.get("view", [""])[0]
            in (
                "traj", "heat", "transition", "flow",
                "roi", "polar", "heading", "metrics", "diag")
            else no_update
        )
        mode = p["mode"][0] if p.get("mode", [""])[0] in ("accuracy", "speed") else no_update
        view_layout = (
            p["layout"][0]
            if p.get("layout", [""])[0] in ("sections", "compare")
            else no_update
        )
        angle_source = (p["pang"][0]
                        if p.get("pang", [""])[0] in ("orientation", "movement")
                        else no_update)
    
        vp = no_update
        if all(k in p for k in ("vbx0", "vbx1", "vby0", "vby1")):
            try:
                vp = {"xaxis": [float(p["vbx0"][0]), float(p["vbx1"][0])],
                      "yaxis": [float(p["vby0"][0]), float(p["vby1"][0])]}
            except Exception:
                vp = no_update
    
        velocity_range = range_pair("vrmin", "vrmax")
        rings = no_update
        if "loops" in p:
            try:
                raw_rings = json.loads(p["loops"][0])
                if isinstance(raw_rings, list) and raw_rings:
                    cleaned = []
                    for index, ring in enumerate(raw_rings):
                        if not isinstance(ring, dict):
                            continue
                        radius = float(ring.get("radius", 3))
                        x_value = float(ring.get("x", 0))
                        z_value = float(ring.get("z", 0))
                        if not (np.isfinite(radius) and radius > 0
                                and np.isfinite(x_value) and np.isfinite(z_value)):
                            continue
                        cleaned.append({
                            "id": str(ring.get("id", f"ring-{index + 1}")),
                            "name": str(ring.get("name", f"Ring {index + 1}")),
                            "x": x_value, "z": z_value, "radius": radius,
                        })
                    if cleaned:
                        rings = cleaned
            except Exception:
                rings = no_update
        active_ring = s("lactive")
        match_mode = (
            p["lmode"][0]
            if p.get("lmode", [""])[0] in ("any", "all")
            else no_update
        )
        custom_regions = no_update
        if "regions" in p:
            try:
                parsed_regions = json.loads(p["regions"][0])
                cleaned_regions = _normalise_custom_regions(parsed_regions)
                if cleaned_regions:
                    custom_regions = cleaned_regions
            except Exception:
                custom_regions = no_update
        color_value = s("color")
        if color_value is not no_update:
            color_value = str(color_value).lower()
            if color_value == "one":
                color_value = "categorical"
            elif color_value == "gray":
                color_value = "none"
            elif color_value not in {
                "categorical", "none", "individual", "config", "scene", "vr",
                "folder", "roi", "trial", "local_time", "velocity",
                "tortuosity",
            }:
                color_value = "categorical"
        return (
            s("glob"), num("vel"), num("disp"), num("trim"), jump_ms(),
            s("groupby"), s("pool"), color_value, anim, rebase,
            num("hbin"), s("hscale"), num("hbound"), s("hmetric"),
            num("hcmin"), num("hcmax"), s("hcrange"),
            lst("fcfg"), lst("fvr"), lst("ffly"), lst("fscn"), lst("ffld"),
            num("tmin"), num("tmax"), num("smin"), num("smax"),
            lst("raw"), num("ncols"), num("pts"),
            r_range(),
            velocity_range, num("vrmin"), num("vrmax"),
            range_pair("drmin", "drmax"),
            range_pair("wrmin", "wrmax"),
            heat_color_slider_range(),
            trial_slider_range(),
            step_slider_range(),
            num("pmin"), num("amin"), angle_source,
            heading_time_enabled, heading_time_mode,
            heading_time_representation, heading_time_window,
            heading_time_variability, heading_time_angle_bin,
            mode, view, view_layout, vp,
            positive_num("frad"), positive_num("reach"),
            display_percent(), loop_enabled, finite_num("lx"), finite_num("lz"),
            positive_num("lr"), rings, active_ring, match_mode,
            custom_region_enabled, custom_regions, s("ractive"),
            distribution_mode, distribution_points, stats_unit, paired_lines,
            positive_num("uscale"), s("ulabel"),
            transition_enabled, transition_outcome, transition_metric,
            finite_num("tcmin"), finite_num("tcmax"),
            finite_num("tsplit"),
            positive_num("trnmin"),
            minimal_layout, True,
        )
    
    
    @app.callback(
        Output("btn-load", "n_clicks"),
        Input("autoload-interval", "n_intervals"),
        State("glob-input", "value"),
        State("btn-load", "n_clicks"),
        prevent_initial_call=True,
    )
    def auto_trigger(n_intervals, glob_val, clicks):
        if glob_val and glob_val.strip():
            return (clicks or 0) + 1
        return no_update
    
    
    # Dropped folder -> resolve to a glob, fill the input, and auto-load.
    @app.callback(
        Output("glob-input", "value", allow_duplicate=True),
        Output("btn-load", "n_clicks", allow_duplicate=True),
        Output("load-status", "children", allow_duplicate=True),
        Input("drop-data", "data"),
        State("btn-load", "n_clicks"),
        prevent_initial_call=True,
    )
    def on_folder_drop(data, clicks):
        if not data or not data.get("files"):
            return no_update, no_update, "Drop a folder that contains trajectory CSVs."
        pat = resolve_dropped_folder(data.get("folder", ""), data.get("files", []))
        if not pat:
            return (no_update, no_update,
                    f"Could not locate '{data.get('folder','')}' on disk. Enter the folder path instead.")
        workers = min(LOAD_WORKERS, max(1, int(data.get("n") or 1)))
        return (
            pat,
            (clicks or 0) + 1,
            f"Data source resolved: {pat} · starting {workers} parallel "
            f"worker{'s' if workers != 1 else ''}…",
        )
    
    
    # Start polling the unified header status the moment work is requested.  The
    # queued snapshot prevents an early interval tick from putting the poller back
    # to sleep before the worker thread reaches its first progress update.
    @app.callback(
        Output("load-progress-interval", "disabled", allow_duplicate=True),
        Output("load-progress-interval", "n_intervals", allow_duplicate=True),
        Input("btn-load", "n_clicks"),
        Input("btn-plot", "n_clicks"),
        Input("btn-export", "n_clicks"),
        Input("polar-moving", "value"),
        Input("polar-walk", "value"),
        Input("polar-angle-source", "value"),
        Input("polar-r-range", "value"),
        Input("polar-min-point-frac", "value"),
        Input("polar-min-animal-frac", "value"),
        Input("heatmap-metric", "value"),
        Input("heatmap-scale", "value"),
        Input("heatmap-cmin", "value"),
        Input("heatmap-cmax", "value"),
        Input("heatmap-crange", "value"),
        Input("heatmap-binsize", "value"),
        Input("heatmap-bound", "value"),
        Input("color-by", "value"),
        Input("render-mode", "value"),
        Input("animate-toggle", "value"),
        Input("plot-points", "value"),
        State("store-glob", "data"),
        State("view-render-state", "data"),
        prevent_initial_call=True,
    )
    def start_progress(_load_n, _plot_n, _export_n, _moving, _walk, _angle,
                       _r_range, _point_frac, _animal_frac, _hm_metric, _hm_scale,
                       _hm_cmin, _hm_cmax, _hm_crange, _hm_binsize, _hm_bound,
                       _color_by, _render_mode, _animate, _plot_points,
                       pattern, render_state):
        trigger = ctx.triggered_id
        is_direction = (
            str(trigger).startswith("polar-")
            or trigger in {
                "heatmap-metric", "heatmap-scale", "heatmap-cmin",
                "heatmap-cmax", "heatmap-crange",
            }
        )
        if is_direction and (not pattern or not render_state):
            return True, 0
        if trigger in {"heatmap-binsize", "heatmap-bound"} and (
                not pattern or not render_state):
            return True, 0
        if trigger in {"color-by", "render-mode", "animate-toggle", "plot-points"} and (
                not pattern or not render_state):
            return True, 0
        labels = {
            "btn-load": (
                "request",
                "Started — preprocessing files in parallel background workers; "
                "plots and statistics will finish in separate stages.",
            ),
            "btn-plot": ("request", "Queuing plot update…"),
            "btn-export": ("request", "Queuing offline HTML export…"),
            "heatmap-binsize": ("spatial-grid", "Queuing spatial re-binning…"),
            "heatmap-bound": ("spatial-grid", "Queuing spatial extent update…"),
            "color-by": ("drawing", "Queuing trajectory colour update…"),
            "render-mode": ("drawing", "Queuing drawing-mode update…"),
            "animate-toggle": ("drawing", "Queuing trajectory playback update…"),
            "plot-points": ("drawing", "Queuing drawing-budget update…"),
        }
        kind, message = labels.get(
            trigger, ("request", "Queuing direction-field update…")
        )
        _progress_arm(kind, message)
        # Reset the bounded poller for every operation.  The interval itself is
        # capped, so a delayed response can never queue an unbounded request
        # backlog while a large figure is serialising in another thread.
        return False, 0
    
    
    # Poll loading and rendering progress into the single header status system.
    @app.callback(
        Output("operation-progress", "data"),
        Output("status-progress-bar", "style"),
        Output("status-progress-text", "children"),
        Output("load-status", "children", allow_duplicate=True),
        Output("load-progress-interval", "disabled", allow_duplicate=True),
        Input("load-progress-interval", "n_intervals"),
        prevent_initial_call=True,
    )
    def tick_progress(n):
        p = _progress_snapshot()
        total = max(1, int(p.get("total") or 1))
        done = max(0, int(p.get("done") or 0))
        active = bool(p.get("active"))
        stages = p.get("stages") or []
        if active and stages:
            completed = sum(stage.get("status") == "done" for stage in stages)
            current_fraction = done / total
            pct = int(round(100 * (completed + current_fraction) / len(stages)))
        else:
            pct = 100
        pct = max(0, min(100, pct))
        style = {"width": f"{pct if active else 100}%"}
        progress_text = (
            f"{pct}% · {p.get('phase', 'Working')}"
            if active else p.get("phase", "Ready")
        )
        status = p.get("message") or no_update
        # Each operation arm resets the interval, including the load -> render
        # hand-off. Stop on the first completed snapshot so already-rendered
        # pages do not keep issuing progress requests in the background.
        disable = not active
        return p, style, progress_text, status, disable
    
    
    @app.callback(
        Output("url", "search"),
        Input("btn-plot", "n_clicks"),
        Input("glob-input", "value"),
        Input("vel-threshold", "value"),
        Input("min-disp", "value"),
        Input("trim-samples", "value"),
        Input("jump-buffer", "value"),
        Input("group-by", "value"),
        Input("pool-mode", "value"),
        Input("color-by", "value"),
        Input("animate-toggle", "value"),
        Input("heatmap-binsize", "value"),
        Input("heatmap-scale", "value"),
        Input("heatmap-bound", "value"),
        Input("heatmap-metric", "value"),
        Input("heatmap-cmin", "value"),
        Input("heatmap-cmax", "value"),
        Input("heatmap-crange", "value"),
        Input("filter-configs", "value"),
        Input("filter-vrs", "value"),
        Input("filter-flyids", "value"),
        Input("filter-scenes", "value"),
        Input("filter-folders", "value"),
        Input("trial-min", "value"),
        Input("trial-max", "value"),
        Input("step-min", "value"),
        Input("step-max", "value"),
        Input("raw-columns", "value"),
        Input("subplot-ncols", "value"),
        Input("plot-points", "value"),
        Input("polar-r-range", "value"),
        Input("vel-range-effective", "data"),
        Input("disp-range", "value"),
        Input("walk-range", "value"),
        Input("polar-min-point-frac", "value"),
        Input("polar-min-animal-frac", "value"),
        Input("polar-angle-source", "value"),
        Input("heading-time-enabled", "value"),
        Input("heading-time-mode", "value"),
        Input("heading-time-representation", "value"),
        Input("heading-time-window", "value"),
        Input("heading-time-variability", "value"),
        Input("heading-time-angle-bin", "value"),
        Input("render-mode", "value"),
        Input("view-mode", "value"),
        Input("view-layout", "value"),
        Input("roi-reach", "value"),
        Input("flow-max-radius", "value"),
        Input("traj-trial-fraction", "value"),
        Input("loop-enabled", "value"),
        Input("loop-x", "value"),
        Input("loop-z", "value"),
        Input("loop-radius", "value"),
        Input("loop-rings-store", "data"),
        Input("loop-active-ring", "value"),
        Input("loop-match-mode", "value"),
        Input("custom-region-enabled", "value"),
        Input("custom-regions-store", "data"),
        Input("custom-region-active", "value"),
        Input("distribution-mode", "value"),
        Input("distribution-show-points", "value"),
        Input("stats-unit", "value"),
        Input("observation-paired-lines", "value"),
        Input("spatial-unit-scale", "value"),
        Input("spatial-unit-label", "value"),
        Input("transition-enabled", "value"),
        Input("transition-outcome", "value"),
        Input("transition-metric", "value"),
        Input("transition-count-min", "value"),
        Input("transition-count-max", "value"),
        Input("transition-split-z", "value"),
        Input("transition-min-trials", "value"),
        Input("minimal-layout-store", "data"),
        State("viewport-store", "data"),
        State("url-restored", "data"),
        prevent_initial_call=True,
    )
    def update_url(n, g, vel, disp, trim, jb, gb, pm, color, anim,
                   hbin, hscale, hbound, hmetric, hcmin, hcmax, hcrange,
                   fcfg, fvr, ffly, fscn, ffld, tmin, tmax, smin, smax, raw, ncols, pts,
                   rrange, vrange, drange, wrange, pmin, amin, angle_source,
                   heading_time_enabled, heading_time_mode,
                   heading_time_representation, heading_time_window,
                   heading_time_variability, heading_time_angle_bin,
                   mode, view,
                   view_layout, reach, flow_max_radius, traj_fraction,
                   loop_enabled, loop_x, loop_z, loop_radius,
                   loop_rings, loop_active, loop_match_mode,
                   custom_region_enabled, custom_regions, custom_region_active,
                   distribution_mode, distribution_show_points, stats_unit,
                   observation_paired_lines,
                   spatial_unit_scale, spatial_unit_label,
                   transition_enabled, transition_outcome, transition_metric,
                   transition_count_min, transition_count_max,
                   transition_split_z, transition_min_trials,
                   minimal_layout, vp, restored):
        if not restored:
            return no_update
        params = {}
        if g:
            params["glob"] = g
        nums = {"vel": vel, "disp": disp, "trim": trim, "jb": jb, "hbin": hbin,
                "hbound": hbound, "hcmin": hcmin, "hcmax": hcmax, "ncols": ncols,
                "pts": pts, "tmin": tmin, "tmax": tmax,
                "smin": smin, "smax": smax, "pmin": pmin, "amin": amin,
                "reach": reach, "frad": flow_max_radius,
                "tf": traj_fraction, "lx": loop_x, "lz": loop_z,
                "lr": loop_radius, "uscale": spatial_unit_scale,
                "htwin": heading_time_window,
                "htbin": heading_time_angle_bin,
                "tcmin": transition_count_min, "tcmax": transition_count_max,
                "tsplit": transition_split_z,
                "trnmin": transition_min_trials}
        for k, v in nums.items():
            if v is not None and v != "":
                if k == "trim" and float(v or 0) <= 0:
                    continue
                params[k] = v
        strs = {"groupby": gb, "pool": pm, "color": color, "mode": mode,
                "hscale": hscale,
                "hmetric": hmetric, "hcrange": hcrange, "pang": angle_source,
                "htmode": heading_time_mode,
                "htview": heading_time_representation,
                "view": view, "layout": view_layout}
        if spatial_unit_label:
            strs["ulabel"] = spatial_unit_label
        if transition_outcome in TRANSITION_OUTCOMES:
            strs["tmode"] = transition_outcome
        if transition_metric in TRANSITION_METRICS:
            strs["tmetric"] = transition_metric
        for k, v in strs.items():
            if v:
                params[k] = v
        params["anim"] = "1" if (anim and "on" in anim) else "0"
        params["loop"] = "1" if _on(loop_enabled) else "0"
        params["region"] = "1" if _on(custom_region_enabled) else "0"
        params["trans"] = "1" if _on(transition_enabled) else "0"
        params["heading"] = "1" if _on(heading_time_enabled) else "0"
        params["htvar"] = "1" if _on(heading_time_variability) else "0"
        params["dpts"] = "1" if _on(distribution_show_points) else "0"
        params["pairs"] = "1" if _on(observation_paired_lines) else "0"
        params["minimal"] = "1" if minimal_layout else "0"
        if loop_rings:
            params["loops"] = json.dumps(
                loop_rings, separators=(",", ":"), ensure_ascii=False)
        if loop_active:
            params["lactive"] = str(loop_active)
        if loop_match_mode in ("any", "all"):
            params["lmode"] = loop_match_mode
        if custom_regions:
            params["regions"] = json.dumps(
                custom_regions, separators=(",", ":"), ensure_ascii=False)
        if custom_region_active:
            params["ractive"] = str(custom_region_active)
        if distribution_mode in ("auto", "swarm", "violin"):
            params["dist"] = distribution_mode
        if stats_unit in ("trial", "animal"):
            params["sunit"] = stats_unit
        lists = {"fcfg": fcfg, "fvr": fvr, "ffly": ffly, "fscn": fscn, "ffld": ffld, "raw": raw}
        for k, v in lists.items():
            if v:
                params[k] = ",".join(str(x) for x in v)
        if vp and not vp.get("reset") and "xaxis" in vp and "yaxis" in vp:
            params["vbx0"], params["vbx1"] = vp["xaxis"]
            params["vby0"], params["vby1"] = vp["yaxis"]
        lo, hi = _polar_r_range(rrange)
        if lo > 0 or hi < 1:
            params["rmin"], params["rmax"] = lo, hi
        for prefix, value in (("vr", vrange), ("dr", drange), ("wr", wrange)):
            rng = _numeric_range(value)
            if rng:
                params[f"{prefix}min"], params[f"{prefix}max"] = rng
        return "?" + urlencode(params) if params else ""
    
    
    @app.callback(
        Output("auto-replot-state", "data"),
        Output("auto-replot-interval", "disabled"),
        Output("auto-replot-interval", "n_intervals"),
        Output("plot-status", "children", allow_duplicate=True),
        Input("vel-threshold", "value"),
        Input("min-disp", "value"),
        Input("trim-samples", "value"),
        Input("jump-buffer", "value"),
        Input("group-by", "value"),
        Input("pool-mode", "value"),
        Input("filter-configs", "value"),
        Input("filter-vrs", "value"),
        Input("filter-flyids", "value"),
        Input("filter-scenes", "value"),
        Input("filter-folders", "value"),
        Input("vel-range-effective", "data"),
        Input("disp-range", "value"),
        Input("trial-range", "value"),
        Input("trial-min", "value"),
        Input("trial-max", "value"),
        Input("step-range", "value"),
        Input("step-min", "value"),
        Input("step-max", "value"),
        Input("raw-columns", "value"),
        Input("subplot-ncols", "value"),
        Input("roi-reach", "value"),
        Input("roi-entered", "value"),
        Input("roi-trim", "value"),
        State("data-generation", "data"),
        State("store-glob", "data"),
        State("btn-plot", "n_clicks"),
        State("view-render-state", "data"),
        prevent_initial_call=True,
    )
    def arm_auto_replot(*values):
        generation, pattern, clicks, render_state = values[-4:]
        if not pattern:
            return no_update, True, 0, no_update
        if isinstance(generation, dict):
            loaded = float(generation.get("loaded") or 0)
            rendered = float((render_state or {}).get("completed") or 0)
            # Data-dependent range controls and filter options settle before the
            # range-control load barrier launches the first master render.  Base
            # this guard on operation ordering, not wall-clock time: on a busy or
            # remote browser those callbacks can arrive well after any fixed
            # grace period and otherwise launch a duplicate full render.
            if loaded and rendered < loaded:
                return no_update, True, 0, no_update
        trigger = ctx.triggered_id or "control"
        label = {
            "trial-range": "trial subset", "trial-min": "trial subset",
            "trial-max": "trial subset", "step-range": "step subset",
            "step-min": "step subset", "step-max": "step subset",
            "vel-range": "velocity subset", "disp-range": "displacement subset",
            "walk-range": "distance-walked subset",
            "filter-configs": "config subset", "filter-vrs": "VR subset",
            "filter-flyids": "animal subset", "filter-scenes": "scene subset",
            "filter-folders": "folder subset",
        }.get(str(trigger), str(trigger).replace("-", " "))
        return (
            {"clicks": int(clicks or 0), "trigger": str(trigger), "ts": time.time()},
            False,
            0,
            f"Queued {label} update ({PLOT_DEBOUNCE_MS / 1000:g}s idle).",
        )
    
    
    @app.callback(
        Output("btn-plot", "n_clicks", allow_duplicate=True),
        Output("auto-replot-interval", "disabled", allow_duplicate=True),
        Output("plot-status", "children", allow_duplicate=True),
        Input("auto-replot-interval", "n_intervals"),
        State("auto-replot-state", "data"),
        State("btn-plot", "n_clicks"),
        State("store-glob", "data"),
        prevent_initial_call=True,
    )
    def fire_auto_replot(n, armed, clicks, pattern):
        # Arming the debounce resets n_intervals to zero.  That zero-value update
        # reaches this callback immediately; leave the interval enabled so it can
        # produce the first real tick instead of cancelling itself before 750 ms.
        if not n:
            return no_update, no_update, no_update
        if not pattern or not armed:
            return no_update, True, no_update
        if int(clicks or 0) > int((armed or {}).get("clicks") or 0):
            return no_update, True, "Manual update applied."
        return (clicks or 0) + 1, True, "Auto-updating after idle change..."
    
    
    @app.callback(
        Output("load-status", "children"),
        Output("store-glob", "data"),
        Output("data-generation", "data"),
        Output("filter-configs", "options"),
        Output("filter-vrs", "options"),
        Output("filter-flyids", "options"),
        Output("filter-scenes", "options"),
        Output("filter-folders", "options"),
        Output("raw-columns", "options"),
        Output("metadata-display", "children"),
        Output("vel-histogram", "figure"),
        Output("disp-histogram", "figure"),
        Output("initial-heading-plot", "figure"),
        Output("heatmap-binsize", "value", allow_duplicate=True),
        Output("btn-plot", "n_clicks"),
        Output("auto-thresholds", "data"),
        Output("view-render-state", "data", allow_duplicate=True),
        Output("viewport-store", "data", allow_duplicate=True),
        Input("btn-load", "n_clicks"),
        State("glob-input", "value"),
        State("btn-plot", "n_clicks"),
        State("heatmap-binsize", "value"),
        State("store-glob", "data"),
        prevent_initial_call=True,
    )
    def load_data_cb(n_clicks, pattern, plot_clicks, cur_binsize, previous_pattern):
        empty = go.Figure().update_layout(height=190, template="plotly_white")
        empty_heading = _msg_figure("Load data to inspect raw starting headings.", 360)
        nope = ("Choose a folder or enter a CSV glob.", None, None, [], [], [], [], [], [], "",
                empty, empty, empty_heading,
                no_update, no_update, None, {}, no_update)
        if not pattern:
            LOGGER.warning("ui.load_rejected reason=missing_source")
            return nope
    
        t0 = time.time()
        LOGGER.info("ui.load_request click=%s source=%r", n_clicks, pattern)
        df, stats, metas = _load_data(pattern)
        elapsed = time.time() - t0
    
        if df is None or len(df) == 0:
            LOGGER.warning("ui.load_empty source=%r", pattern)
            return (f"No trajectory CSVs matched the current data source.", None, None, [], [], [], [], [], [], "",
                    empty, empty, empty_heading, no_update, no_update, None, {}, {"reset": True})
    
        n_files = df["SourceFile"].nunique()
        n_segs = df["_seg_id"].nunique()
        raw_rows = int(df.attrs.get("_raw_rows", len(df)))
        retained_pct = 100.0 * len(df) / max(raw_rows, 1)
        status = (
            f"Ready — {n_files} files | {len(df):,}/{raw_rows:,} rows retained "
            f"({retained_pct:.1f}%) | {n_segs} segments | {elapsed:.1f}s"
        )
        LOGGER.info(
            "ui.load_ready rows=%d files=%d segments=%d seconds=%.3f reset_controls=%s",
            len(df), n_files, n_segs, elapsed,
            bool(previous_pattern) and _pattern_key(previous_pattern) != _pattern_key(pattern),
        )
    
        def opts(col):
            if col not in df.columns:
                return []
            group_kind = {
                "ConfigFile": "config",
                "SceneName": "scene",
                "VR": "vr",
                "FlyID": "flyid",
                "SourceFolder": "file",
            }.get(col, "config")
            vals = _ordered_group_values(df[col].unique(), group_kind)
            if col == "ConfigFile":
                return [{"label": humanise_config(v), "value": v, "title": v} for v in vals]
            return [{"label": str(v), "value": v} for v in vals]
    
        num_cols = sorted([c for c in df.columns
                           if df[c].dtype in (np.float64, np.int64, np.float32, np.int32)
                           and c not in ("CurrentTrial", "CurrentStep")])
        col_opts = [{"label": c, "value": c} for c in num_cols]
    
        meta_parts = []
        for m in metas[:5]:
            fm = m.get("fly_metadata")
            if not fm:
                continue
            meta_parts.append(f"--- {m['folder']} ---")
            for k in ("ExperimenterName", "Comments"):
                if fm.get(k):
                    meta_parts.append(f"  {k}: {fm[k]}")
            for fly in fm.get("Flies", []):
                meta_parts.append(f"  {fly.get('VR','')}: fly{fly.get('FlyID','')}"
                                  f" {fly.get('Sex','')}")
    
        token = _DATA_TOKEN_BY_PATTERN.get(_pattern_key(pattern))
        reset_controls = (bool(previous_pattern)
                          and _pattern_key(previous_pattern) != _pattern_key(pattern))
        vv = _VELOCITY_CACHE.get(token)
        if vv is None:
            vv = smoothed_velocity(df, 10)
            if token is not None:
                _VELOCITY_CACHE[token] = vv
        vv = vv[np.isfinite(vv)]
        vel_fig = build_velocity_histogram(df, velocity_values=vv)
        disp_fig = build_displacement_histogram(stats)
        heading_fig = build_initial_heading_distribution(df)
    
        # Auto filter defaults: 99th-pct velocity, and 5% of the median net
        # displacement (a scale-free "barely moved" cut). Stored for the auto boxes.
        disp = stats["displacement"].to_numpy() if stats is not None and len(stats) else np.array([])
        auto = {"vel": round(float(np.percentile(vv, 99)), 3) if vv.size else None,
                "disp": round(float(0.05 * np.median(disp)), 3) if disp.size else None}
    
        # Smart default bin size on a fresh load; respect any value already set
        # (e.g. restored from the URL).
        binsize_out = no_update if (cur_binsize not in (None, "")) else default_bin_size(df)
    
        return (
            status, pattern,
            {"pattern": pattern, "token": repr(token), "loaded": time.time(),
             "reset_controls": reset_controls, "raw_rows": raw_rows,
             "retained_rows": len(df), "retained_pct": retained_pct},
            opts("ConfigFile"), opts("VR"), opts("FlyID"), opts("SceneName"),
            opts("SourceFolder"), col_opts,
            "\n".join(meta_parts) or "No experiment metadata found.",
            vel_fig, disp_fig, heading_fig, binsize_out,
            no_update, auto, no_update,
            {"reset": True} if reset_controls else no_update,
        )
    
    
    @app.callback(
        Output("vel-range", "min"),
        Output("vel-range", "max"),
        Output("vel-range", "step"),
        Output("vel-range", "marks"),
        Output("vel-range", "value"),
        Output("vel-range-hist", "figure"),
        Output("disp-range", "min"),
        Output("disp-range", "max"),
        Output("disp-range", "step"),
        Output("disp-range", "marks"),
        Output("disp-range", "value"),
        Output("disp-range-hist", "figure"),
        Output("walk-range", "min"),
        Output("walk-range", "max"),
        Output("walk-range", "step"),
        Output("walk-range", "marks"),
        Output("walk-range", "value"),
        Output("walk-range-hist", "figure"),
        Output("trial-range", "min"),
        Output("trial-range", "max"),
        Output("trial-range", "step"),
        Output("trial-range", "marks"),
        Output("trial-range", "value"),
        Output("trial-range-hist", "figure"),
        Output("step-range", "min"),
        Output("step-range", "max"),
        Output("step-range", "step"),
        Output("step-range", "marks"),
        Output("step-range", "value"),
        Output("step-range-hist", "figure"),
        Output("btn-plot", "n_clicks", allow_duplicate=True),
        Input("data-generation", "data"),
        State("store-glob", "data"),
        State("vel-range", "value"),
        State("disp-range", "value"),
        State("walk-range", "value"),
        State("trial-range", "value"),
        State("trial-min", "value"),
        State("trial-max", "value"),
        State("step-range", "value"),
        State("step-min", "value"),
        State("step-max", "value"),
        State("btn-plot", "n_clicks"),
        prevent_initial_call=True,
    )
    def update_range_controls(generation, pattern, vel_current, disp_current,
                              walk_current, trial_current,
                              trial_min, trial_max, step_current, step_min, step_max,
                              plot_clicks):
        empty = build_mini_histogram(None)
        defaults = (0, 1, 0.01, {0: "0", 1: "1"}, [0, 1], empty)
        if not pattern:
            return defaults + defaults + defaults + defaults + defaults + (no_update,)
        df, stats, _ = _load_data(pattern)
        if df is None or len(df) == 0 or stats is None:
            return defaults + defaults + defaults + defaults + defaults + (no_update,)
    
        reset_controls = bool((generation or {}).get("reset_controls"))
        vel_payload = _range_control_payload(
            stats["peak_velocity"].to_numpy() if "peak_velocity" in stats else [],
            None if reset_controls else vel_current,
            color="#1f77b4",
            floor_zero=True,
            upper_pct=99.0,
        )
        disp_payload = _range_control_payload(
            stats["displacement"].to_numpy() if "displacement" in stats else [],
            None if reset_controls else disp_current,
            color="#2ca02c",
            floor_zero=True,
        )
        walk_payload = _range_control_payload(
            stats["distance_walked"].to_numpy()
            if "distance_walked" in stats else [],
            None if reset_controls else walk_current,
            color="#7c3aed",
            floor_zero=True,
        )
    
        trial_values = pd.to_numeric(df["CurrentTrial"], errors="coerce").to_numpy(dtype=float)
        lo, hi = _range_bounds(trial_values, floor_zero=False, upper_pct=None)
        restored_trial = _trial_range(trial_min, trial_max)
        trial_source = None if reset_controls else trial_current
        if restored_trial and _looks_like_initial_range(_numeric_range(trial_current), lo, hi):
            trial_source = [lo if restored_trial[0] is None else restored_trial[0],
                            hi if restored_trial[1] is None else restored_trial[1]]
        trial_value = _range_control_value(trial_source, lo, hi)
        trial_payload = (
            float(lo),
            float(hi),
            1,
            _slider_marks(lo, hi),
            trial_value,
            build_mini_histogram(trial_values, trial_value, color="#b7791f",
                                 x_range=(lo, hi)),
        )
        step_values = pd.to_numeric(df["CurrentStep"], errors="coerce").to_numpy(dtype=float)
        slo, shi = _range_bounds(step_values, floor_zero=False, upper_pct=None)
        restored_step = _value_range(step_min, step_max)
        step_source = None if reset_controls else step_current
        if restored_step and _looks_like_initial_range(_numeric_range(step_current), slo, shi):
            step_source = [slo if restored_step[0] is None else restored_step[0],
                           shi if restored_step[1] is None else restored_step[1]]
        step_value = _range_control_value(step_source, slo, shi)
        step_payload = (
            float(slo), float(shi), 1, _slider_marks(slo, shi), step_value,
            build_mini_histogram(step_values, step_value, color="#0f766e",
                                 x_range=(slo, shi)),
        )
        # This click is the load barrier: all slider outputs in this response are
        # applied before the master renderer reads them as State.  Triggering the
        # renderer directly from data-generation allowed it to race stale ranges
        # from the previous dataset.
        return (vel_payload + disp_payload + walk_payload
                + trial_payload + step_payload
                + ((plot_clicks or 0) + 1,))
    
    
    @app.callback(
        Output("vel-range-effective", "data"),
        Input("vel-range", "value"),
        Input("vel-range-min", "value"),
        Input("vel-range-max", "value"),
    )
    def effective_velocity_range(slider_value, exact_min, exact_max):
        """Combine the robust visual slider with optional unbounded exact inputs."""
    
        slider = _numeric_range(slider_value) or (0.0, 1.0)
        if exact_min in (None, "") and exact_max in (None, ""):
            return {"range": list(slider), "explicit": False}
        try:
            lo = slider[0] if exact_min in (None, "") else float(exact_min)
            hi = slider[1] if exact_max in (None, "") else float(exact_max)
        except (TypeError, ValueError):
            return {"range": list(slider), "explicit": False}
        if not (np.isfinite(lo) and np.isfinite(hi)):
            return {"range": list(slider), "explicit": False}
        return {"range": [min(lo, hi), max(lo, hi)], "explicit": True}
    
    
    def _register_editable_range_sync(prefix: str) -> None:
        """Register a browser-local two-way slider ↔ plain-number synchroniser."""
        app.clientside_callback(
            """
            function(value, exactMin, exactMax, fullMin, fullMax) {
              var no=window.dash_clientside.no_update;
              var cc=window.dash_clientside.callback_context||{};
              var trigger=String(cc.triggered_id||'');
              var current=Array.isArray(value)&&value.length>=2
                ? [Number(value[0]),Number(value[1])] : [Number(fullMin),Number(fullMax)];
              function valid(number){return Number.isFinite(Number(number));}
              function label(number){
                number=Number(number);var absolute=Math.abs(number);
                if(absolute>=1000000)return (number/1000000).toFixed(1)+'M';
                if(absolute>=1000)return (number/1000).toFixed(1)+'K';
                if(absolute>=100)return number.toFixed(0);
                if(absolute>=10)return Number(number.toFixed(1)).toString();
                return Number(number.toPrecision(2)).toString();
              }
              function marks(lo,hi){
                var out={};[lo,(lo+hi)/2,hi].forEach(function(number){
                  out[Number(number.toFixed(6))]=label(number);
                });return out;
              }
              if(trigger.indexOf('-range')>=0 &&
                 trigger.indexOf('-range-min')<0 &&
                 trigger.indexOf('-range-max')<0) {
                if(!current.every(valid))return [no,no,no,no,no,no];
                return [no,no,no,no,current[0],current[1]];
              }
              if(trigger.indexOf('-range-min')>=0 ||
                 trigger.indexOf('-range-max')>=0) {
                var lo=valid(exactMin)?Number(exactMin):current[0];
                var hi=valid(exactMax)?Number(exactMax):current[1];
                if(!valid(lo)||!valid(hi))return [no,no,no,no,no,no];
                if(lo>hi){var swap=lo;lo=hi;hi=swap;}
                var boundLo=Math.min(Number(fullMin),lo);
                var boundHi=Math.max(Number(fullMax),hi);
                return [
                  boundLo,boundHi,marks(boundLo,boundHi),[lo,hi],lo,hi
                ];
              }
              return [no,no,no,no,no,no];
            }
            """,
            Output(f"{prefix}-range", "min", allow_duplicate=True),
            Output(f"{prefix}-range", "max", allow_duplicate=True),
            Output(f"{prefix}-range", "marks", allow_duplicate=True),
            Output(f"{prefix}-range", "value", allow_duplicate=True),
            Output(f"{prefix}-range-min", "value", allow_duplicate=True),
            Output(f"{prefix}-range-max", "value", allow_duplicate=True),
            Input(f"{prefix}-range", "value"),
            Input(f"{prefix}-range-min", "value"),
            Input(f"{prefix}-range-max", "value"),
            State(f"{prefix}-range", "min"),
            State(f"{prefix}-range", "max"),
            prevent_initial_call="initial_duplicate",
        )
    
    
    _register_editable_range_sync("vel")
    _register_editable_range_sync("disp")
    _register_editable_range_sync("walk")
    
    
    @app.callback(
        Output("vel-range-hist", "figure", allow_duplicate=True),
        Output("disp-range-hist", "figure", allow_duplicate=True),
        Output("walk-range-hist", "figure", allow_duplicate=True),
        Output("trial-range-hist", "figure", allow_duplicate=True),
        Output("step-range-hist", "figure", allow_duplicate=True),
        Input("vel-range-effective", "data"),
        Input("disp-range", "value"),
        Input("walk-range", "value"),
        Input("trial-range", "value"),
        Input("step-range", "value"),
        State("store-glob", "data"),
        prevent_initial_call=True,
    )
    def update_range_hist_selection(
            vel_range, disp_range, walk_range, trial_range, step_range, pattern):
        if not pattern:
            return no_update, no_update, no_update, no_update, no_update
        df, stats, _ = _load_data(pattern)
        if df is None or stats is None:
            return no_update, no_update, no_update, no_update, no_update
        vel_values = stats["peak_velocity"].to_numpy() if "peak_velocity" in stats else []
        disp_values = stats["displacement"].to_numpy() if "displacement" in stats else []
        walk_values = (
            stats["distance_walked"].to_numpy()
            if "distance_walked" in stats else [])
        trial_values = pd.to_numeric(df["CurrentTrial"], errors="coerce").to_numpy(dtype=float)
        step_values = pd.to_numeric(df["CurrentStep"], errors="coerce").to_numpy(dtype=float)
        return (
            build_mini_histogram(vel_values, vel_range, color="#1f77b4",
                                 x_range=_range_bounds(
                                     vel_values, floor_zero=True, upper_pct=99.0)),
            build_mini_histogram(disp_values, disp_range, color="#2ca02c",
                                 x_range=_range_bounds(disp_values, floor_zero=True)),
            build_mini_histogram(walk_values, walk_range, color="#7c3aed",
                                 x_range=_range_bounds(walk_values, floor_zero=True)),
            build_mini_histogram(trial_values, trial_range, color="#b7791f",
                                 x_range=_range_bounds(trial_values, floor_zero=False,
                                                       upper_pct=None)),
            build_mini_histogram(step_values, step_range, color="#0f766e",
                                 x_range=_range_bounds(step_values, floor_zero=False,
                                                       upper_pct=None)),
        )
    
    
    def _input_number(value):
        try:
            v = float(value)
        except Exception:
            return None
        return int(v) if v.is_integer() else v
    
    
    @app.callback(
        Output("trial-min", "value", allow_duplicate=True),
        Output("trial-max", "value", allow_duplicate=True),
        Input("trial-range", "value"),
        State("trial-range", "min"),
        State("trial-range", "max"),
        prevent_initial_call=True,
    )
    def sync_trial_range_to_inputs(value, full_min, full_max):
        return _range_slider_to_open_bounds(value, full_min, full_max)
    
    
    @app.callback(
        Output("step-min", "value", allow_duplicate=True),
        Output("step-max", "value", allow_duplicate=True),
        Input("step-range", "value"),
        State("step-range", "min"),
        State("step-range", "max"),
        prevent_initial_call=True,
    )
    def sync_step_range_to_inputs(value, full_min, full_max):
        return _range_slider_to_open_bounds(value, full_min, full_max)
    
    
    @app.callback(
        Output("roi-reach", "value", allow_duplicate=True),
        Output("roi-reach-slider", "value"),
        Input("roi-reach", "value"),
        Input("roi-reach-slider", "value"),
        prevent_initial_call=True,
    )
    def sync_roi_reach_controls(exact_value, slider_value):
        """Keep the quick slider and unbounded exact value in sync.
    
        The number input is authoritative for URL/restored values. Values outside
        the slider's visual 0.5–100 span move its handle to the nearest endpoint
        without changing or clipping the exact radius.
        """
        if ctx.triggered_id == "roi-reach-slider":
            return slider_value, no_update
        if ctx.triggered_id == "roi-reach":
            try:
                exact = float(exact_value)
            except (TypeError, ValueError):
                return no_update, no_update
            if not np.isfinite(exact) or exact <= 0:
                return no_update, no_update
            return no_update, min(100.0, max(0.5, exact))
        return no_update, no_update
    
    
    @app.callback(
        Output("heatmap-color-values", "data"),
        Output("heatmap-color-range", "min"),
        Output("heatmap-color-range", "max"),
        Output("heatmap-color-range", "step"),
        Output("heatmap-color-range", "marks"),
        Output("heatmap-color-range", "value"),
        Output("heatmap-color-hist", "figure"),
        # These distributions are derived from the already-computed heatmap bins.
        # Colour-control changes therefore never revisit the source dataframe.
        Input("heatmap-color-distributions", "data"),
        Input("heatmap-metric", "value"),
        Input("heatmap-crange", "value"),
        State("heatmap-color-range", "value"),
        State("heatmap-color-values", "data"),
        prevent_initial_call=True,
    )
    def update_heatmap_color_controls(distributions, metric, mode, current, previous):
        empty = build_mini_histogram(None, color="#0f766e")
        mode = mode or "percentile"
        default_range = [0, 99] if mode == "percentile" else [0, 1]
        default = ({}, default_range[0], default_range[1],
                   1 if mode == "percentile" else 0.01,
                   ({0: "0", 50: "50", 100: "100"} if mode == "percentile"
                    else {0: "0", 1: "1"}), default_range, empty)
        dist = (distributions or {}).get(metric or "time")
        if not dist:
            return default
        values = _finite_values(dist.get("values", []))
        lo = float(dist.get("lo", 0.0))
        hi = float(dist.get("hi", 1.0))
        if not hi > lo:
            hi = lo + 1.0
        store = {**dist, "metric": metric or "time", "mode": mode}
        previous = previous or {}
        prior_mode = previous.get("mode")
        same_metric = previous.get("metric") == (metric or "time")
        current_rng = _numeric_range(current)
        if mode == "percentile":
            if same_metric and prior_mode == "percentile" and current_rng:
                selected = [max(0.0, current_rng[0]), min(100.0, current_rng[1])]
            elif same_metric and prior_mode == "value" and current_rng:
                selected = [_percentile_rank(values, current_rng[0]),
                            _percentile_rank(values, current_rng[1])]
            else:
                selected = [0.0, 99.0]
            selected_out = (no_update if _numeric_range(current) == _numeric_range(selected)
                            else selected)
            return (
                store, 0.0, 100.0, 1.0, {0: "0", 50: "50", 100: "100"},
                selected_out,
                build_percentile_mini_histogram(values, selected, color="#0f766e"),
            )
        if same_metric and prior_mode == "percentile" and current_rng and values.size:
            selected = [float(np.percentile(values, max(0.0, current_rng[0]))),
                        float(np.percentile(values, min(100.0, current_rng[1])))]
        elif same_metric and prior_mode == "value":
            selected = _range_control_value(current, lo, hi)
        else:
            selected = [lo, hi]
        selected_out = (no_update
                        if _numeric_range(current) == _numeric_range(selected)
                        else selected)
        return (
            store,
            float(lo),
            float(hi),
            _slider_step(lo, hi),
            _slider_marks(lo, hi),
            selected_out,
            build_mini_histogram(values, selected, color="#0f766e", x_range=(lo, hi)),
        )
    
    
    @app.callback(
        Output("heatmap-cmin", "value", allow_duplicate=True),
        Output("heatmap-cmax", "value", allow_duplicate=True),
        Output("heatmap-color-hist", "figure", allow_duplicate=True),
        Input("heatmap-color-range", "value"),
        Input("heatmap-crange", "value"),
        State("heatmap-color-values", "data"),
        State("heatmap-cmin", "value"),
        State("heatmap-cmax", "value"),
        prevent_initial_call=True,
    )
    def sync_heatmap_color_range(value, mode, data, current_cmin, current_cmax):
        rng = _numeric_range(value)
        if rng is None:
            return no_update, no_update, no_update
        lo, hi = rng
        values = _finite_values((data or {}).get("values", []))
        if mode == "percentile":
            lo, hi = max(0.0, lo), min(100.0, hi)
            is_full = lo <= 1e-9 and hi >= 100.0 - 1e-9
            cmin = None if is_full or lo <= 1e-9 else _input_number(lo)
            cmax = None if is_full or hi >= 100.0 - 1e-9 else _input_number(hi)
            fig = build_percentile_mini_histogram(
                values, [lo, hi], color="#0f766e")
            return (no_update if cmin == current_cmin else cmin,
                    no_update if cmax == current_cmax else cmax, fig)
        full_lo = float((data or {}).get("lo", lo))
        full_hi = float((data or {}).get("hi", hi))
        span = max(abs(full_hi - full_lo), 1.0)
        eps = span * 1e-9
        fig = build_mini_histogram(values, [lo, hi], color="#0f766e",
                                   x_range=(full_lo, full_hi))
        cmin = None if lo <= full_lo + eps else _input_number(lo)
        cmax = None if hi >= full_hi - eps else _input_number(hi)
        return (no_update if cmin == current_cmin else cmin,
                no_update if cmax == current_cmax else cmax, fig)
    
    
    _PANEL_ORDER_CONTROLS = {
        "config": ("Config / Treatment", "filter-configs"),
        "scene": ("Scene", "filter-scenes"),
        "vr": ("VR", "filter-vrs"),
        "flyid": ("Fly ID", "filter-flyids"),
        "file": ("Source Folder", "filter-folders"),
    }
    
    
    @app.callback(
        Output("panel-order-summary", "children"),
        Output("panel-order-list", "children"),
        Input("group-by", "value"),
        Input("pool-mode", "value"),
        Input("filter-configs", "options"),
        Input("filter-vrs", "options"),
        Input("filter-flyids", "options"),
        Input("filter-scenes", "options"),
        Input("filter-folders", "options"),
        Input("filter-configs", "value"),
        Input("filter-vrs", "value"),
        Input("filter-flyids", "value"),
        Input("filter-scenes", "value"),
        Input("filter-folders", "value"),
        Input("view-render-state", "data"),
    )
    def render_panel_order_list(group_by, pool_mode, config_options, vr_options,
                                fly_options, scene_options, folder_options,
                                configs, vrs, flies, scenes, folders,
                                render_state):
        """Expose the values actually used by the active subplot grouping."""
        option_sets = {
            "config": config_options,
            "scene": scene_options,
            "vr": vr_options,
            "flyid": fly_options,
            "file": folder_options,
        }
        selections = {
            "config": configs,
            "scene": scenes,
            "vr": vrs,
            "flyid": flies,
            "file": folders,
        }
        if pool_mode == "pooled" or group_by == "all":
            return "Plot order · All pooled", [
                html.Li("All Data", style={"padding": "2px 4px"})
            ]
    
        group_by = group_by if group_by in _PANEL_ORDER_CONTROLS else "config"
        group_label, _control_id = _PANEL_ORDER_CONTROLS[group_by]
        options = option_sets.get(group_by) or []
        selected = {str(value) for value in (selections.get(group_by) or [])}
        option_map = {
            str(option.get("value")): str(
                option.get("label", option.get("value")))
            for option in options
        }
        render_state = render_state if isinstance(render_state, dict) else {}
        state_matches = (
            render_state.get("group_by") == group_by
            and render_state.get("pool_mode") == pool_mode
        )
        rendered_values = (
            [str(value) for value in (render_state.get("groups") or [])]
            if state_matches else []
        )
        values = (
            [value for value in rendered_values if value in option_map]
            if rendered_values else list(option_map)
        )
        if selected:
            values = [value for value in values if value in selected]
        values = _ordered_group_values(values, group_by)
        children = []
        for value in values:
            label = _group_label(group_by, value)
            children.append(html.Li(
                str(label), draggable="true",
                **{
                    "data-order-value": value,
                    "data-order-group": group_by,
                    "title": option_map.get(value, value),
                },
                style={
                    "cursor": "grab", "padding": "2px 4px", "marginBottom": "2px",
                    "border": "1px solid #dde2ee", "borderRadius": "3px",
                    "background": "#fff", "lineHeight": "1.2",
                }))
        return f"Plot order · {group_label}", children
    
    
    @app.callback(
        Output("anim-dummy", "children", allow_duplicate=True),
        Input("panel-order-store", "data"),
        prevent_initial_call=True,
    )
    def apply_panel_order(order_data):
        """Persist the browser-local domain order for the next real render."""
        group_by = str((order_data or {}).get("group_by") or "")
        order = (order_data or {}).get("order") or []
        if group_by not in _PANEL_ORDER_CONTROLS or not order:
            return no_update
        rank = {}
        for value in order:
            rank.setdefault(str(value), len(rank))
        _USER_GROUP_ORDERS[group_by] = rank
        return ""
    
    
    # Auto thresholds: when a box is ticked, fill its field with the computed value
    # and disable it; when unticked, re-enable it (blank = no cut). Also triggers a
    # re-filter so the change actually reaches the plots.
    @app.callback(
        Output("vel-threshold", "value"),
        Output("vel-threshold", "disabled"),
        Output("min-disp", "value"),
        Output("min-disp", "disabled"),
        Output("btn-plot", "n_clicks", allow_duplicate=True),
        Input("vel-auto", "value"),
        Input("disp-auto", "value"),
        Input("auto-thresholds", "data"),
        State("btn-plot", "n_clicks"),
        State("store-glob", "data"),
        prevent_initial_call=True,
    )
    def apply_auto_thresholds(vel_auto, disp_auto, auto, clicks, pattern):
        vel_val = (auto or {}).get("vel") if _on(vel_auto) else no_update
        disp_val = (auto or {}).get("disp") if _on(disp_auto) else no_update
        # Loading a dataset refreshes auto-threshold *suggestions*.  With both
        # switches off that must not issue a second btn-plot click in parallel with
        # the range-control load barrier; doing so used to start two identical
        # master renders for epoch 1.  A user toggle still replots, and a new
        # suggestion replots when either automatic cut is actually enabled.
        should_bump = bool(pattern) and (
            ctx.triggered_id != "auto-thresholds" or _on(vel_auto) or _on(disp_auto)
        )
        bump = (clicks or 0) + 1 if should_bump else no_update
        return vel_val, _on(vel_auto), disp_val, _on(disp_auto), bump
    
    
    def _selected_range(sel):
        return _numeric_range(sel)
    
    
    def _value_range(value_min, value_max):
        def val(x):
            if x in (None, ""):
                return None
            try:
                return float(x)
            except Exception:
                return None
    
        lo, hi = val(value_min), val(value_max)
        if lo is None and hi is None:
            return None
        if lo is not None and hi is not None and lo > hi:
            lo, hi = hi, lo
        return (lo, hi)
    
    
    def _trial_range(trial_min, trial_max):
        return _value_range(trial_min, trial_max)
    
    
    def _range_slider_to_open_bounds(value, full_min, full_max):
        rng = _numeric_range(value)
        if rng is None:
            return no_update, no_update
        try:
            full_min = float(full_min)
            full_max = float(full_max)
        except Exception:
            full_min, full_max = rng
        span = max(abs(full_max - full_min), 1.0)
        eps = span * 1e-9
        lo = None if rng[0] <= full_min + eps else _input_number(rng[0])
        hi = None if rng[1] >= full_max - eps else _input_number(rng[1])
        return lo, hi
    
    
    def _animal_count(df) -> int:
        if df is None or len(df) == 0:
            return 0
        cols = [c for c in ("FlyID", "VR") if c in df.columns]
        if not cols:
            return 0
        return int(df[cols].drop_duplicates().shape[0])
    
    
    def _retention_counts(df, cache=None) -> dict[str, int]:
        key = (id(df), int(len(df)) if df is not None else 0)
        if cache is not None and key in cache:
            return cache[key]
        out = {
            "points": int(len(df)) if df is not None else 0,
            "trials": int(df["_seg_id"].nunique()) if df is not None and "_seg_id" in df else 0,
            "animals": _animal_count(df),
        }
        if cache is not None:
            cache[key] = out
        return out
    
    
    def _pct(part, total) -> str:
        if not total:
            return "0.0%"
        return f"{100.0 * float(part) / float(total):.1f}%"
    
    
    def _counts_phrase(c: dict[str, int]) -> str:
        return (f"{_compact_count(c['points'])} pts, "
                f"{_compact_count(c['trials'])} trials, "
                f"{_compact_count(c['animals'])} animals")
    
    
    def _retention_summary(df_all, df_final) -> str:
        base = _retention_counts(df_all)
        final = _retention_counts(df_final)
        discarded = {k: max(0, base[k] - final[k]) for k in base}
        return (
            f"Retained {_compact_count(final['points'])}/{_compact_count(base['points'])} pts "
            f"({_pct(final['points'], base['points'])}); "
            f"{_compact_count(final['trials'])}/{_compact_count(base['trials'])} trials "
            f"({_pct(final['trials'], base['trials'])}); "
            f"{_compact_count(final['animals'])}/{_compact_count(base['animals'])} animals "
            f"({_pct(final['animals'], base['animals'])}). "
            f"Discarded {_counts_phrase(discarded)}."
        )
    
    
    def _filter_stage_row(label: str, before, after, active=True,
                          note: str | None = None, counts_cache=None):
        b = _retention_counts(before, counts_cache)
        a = _retention_counts(after, counts_cache)
        d = {k: max(0, b[k] - a[k]) for k in b}
        status = "active" if active else "inactive"
        return html.Div([
            html.Div([
                html.Strong(label),
                html.Span(status, className=f"filter-stage-status {status}"),
            ], className="filter-stage-head"),
            html.Div(
                f"Retained {_counts_phrase(a)} "
                f"({ _pct(a['points'], b['points']) } pts, "
                f"{ _pct(a['trials'], b['trials']) } trials, "
                f"{ _pct(a['animals'], b['animals']) } animals).",
                className="filter-stage-line"),
            html.Div(f"Discarded {_counts_phrase(d)}.", className="filter-stage-line"),
            html.Div(note, className="filter-stage-note") if note else None,
        ], className="filter-stage")
    
    
    def _filter_detail_children(df_all, vel_thresh, min_disp, trim, jump_buf,
                                cfg, vrs, fids, scenes, folders,
                                vel_sel, disp_sel, walk_sel,
                                trial_min=None, trial_max=None,
                                step_min=None, step_max=None,
                                pattern=None,
                                roi_reach=None, roi_entered=None, roi_trim=None):
        if df_all is None or len(df_all) == 0:
            return "Load data to see retention accounting."
        counts_cache = {}
        rows = [
            html.Div("Serial accounting: each retained/discarded percentage is relative to the previous step.",
                     className="filter-detail-note")
        ]
        cur = df_all
    
        before = cur
        cur = td_grouping.subset_frame(df_all, configs=cfg, vrs=vrs, fly_ids=fids,
                                       scenes=scenes, folders=folders)
        rows.append(_filter_stage_row(
            "Subset selections", before, cur,
            active=bool(cfg or vrs or fids or scenes or folders),
            note="Config, VR, fly, scene, and folder selectors.",
            counts_cache=counts_cache))
    
        trng = _trial_range(trial_min, trial_max)
        before = cur
        if trng:
            cur = td_grouping.subset_frame(cur, trial_range=trng)
        if trng:
            lo, hi = trng
            if lo is None:
                trial_note = f"Keeps CurrentTrial <= {hi:g}."
            elif hi is None:
                trial_note = f"Keeps CurrentTrial >= {lo:g}."
            else:
                trial_note = f"Keeps CurrentTrial {lo:g} to {hi:g}, inclusive."
        else:
            trial_note = None
        rows.append(_filter_stage_row(
            "Trial range", before, cur, active=bool(trng),
            note=trial_note,
            counts_cache=counts_cache))
    
        srng = _value_range(step_min, step_max)
        before = cur
        if srng:
            cur = td_grouping.subset_frame(cur, step_range=srng)
        if srng:
            lo, hi = srng
            if lo is None:
                step_note = f"Keeps CurrentStep <= {hi:g}."
            elif hi is None:
                step_note = f"Keeps CurrentStep >= {lo:g}."
            else:
                step_note = f"Keeps CurrentStep {lo:g} to {hi:g}, inclusive."
        else:
            step_note = None
        rows.append(_filter_stage_row(
            "Step range", before, cur, active=bool(srng), note=step_note,
            counts_cache=counts_cache))
    
        disp_raw_rng = _selected_range(disp_sel)
        walk_raw_rng = _selected_range(walk_sel)
        vel_raw_rng = _selected_range(vel_sel)
        subset_stats = None
        if disp_raw_rng or walk_raw_rng or vel_raw_rng:
            exact_stats = _load_data(pattern)[1] if pattern else None
            if exact_stats is not None and len(exact_stats):
                visible_ids = pd.Index(cur["_seg_id"].astype(str).unique())
                subset_stats = exact_stats[
                    exact_stats["seg_id"].astype(str).isin(visible_ids)
                ]
            else:
                subset_stats = compute_segment_stats(cur)
        disp_rng = _active_stat_range(disp_raw_rng, subset_stats, "displacement")
        walk_rng = _active_stat_range(
            walk_raw_rng, subset_stats, "distance_walked")
        vel_rng = _active_stat_range(vel_sel, subset_stats, "peak_velocity")
        before = cur
        if disp_rng:
            cur = filter_by_stat_range(cur, subset_stats, "displacement", *disp_rng)
        rows.append(_filter_stage_row(
            "Displacement range", before, cur, active=bool(disp_rng),
            note=f"Range {disp_rng[0]:.3g} to {disp_rng[1]:.3g}." if disp_rng else None,
            counts_cache=counts_cache))

        before = cur
        if walk_rng:
            cur = filter_by_stat_range(
                cur, subset_stats, "distance_walked", *walk_rng)
        rows.append(_filter_stage_row(
            "Distance walked range", before, cur, active=bool(walk_rng),
            note=(f"Range {walk_rng[0]:.3g} to {walk_rng[1]:.3g} path units."
                  if walk_rng else None),
            counts_cache=counts_cache))
    
        before = cur
        if vel_rng:
            cur = filter_by_stat_range(cur, subset_stats, "peak_velocity", *vel_rng)
        rows.append(_filter_stage_row(
            "Peak velocity range", before, cur, active=bool(vel_rng),
            note=f"Range {vel_rng[0]:.3g} to {vel_rng[1]:.3g} units/s." if vel_rng else None,
            counts_cache=counts_cache))
    
        before = cur
        if vel_thresh is not None and vel_thresh > 0 and len(cur):
            vel = velocity_all(cur)
            spikes = np.nan_to_num(vel, nan=0.0) > float(vel_thresh)
            if spikes.any():
                seg = cur["_seg_id"].to_numpy()
                t = cur["Current Time"].to_numpy().astype("datetime64[ns]").astype("int64") / 1e9
                cur = cur[_dilate_keep(seg, t, spikes, _jump_buffer_seconds(jump_buf))]
        rows.append(_filter_stage_row(
            "Max velocity", before, cur, active=bool(vel_thresh is not None and vel_thresh > 0),
            note=(f"Removes samples above {float(vel_thresh):g} units/s with "
                  f"{_jump_buffer_seconds(jump_buf) * 1000:g} ms buffer.")
            if vel_thresh is not None and vel_thresh > 0 else None,
            counts_cache=counts_cache))
    
        before = cur
        if min_disp is not None and min_disp > 0 and len(cur):
            grouped = cur.groupby("_seg_id", sort=False)
            x0 = grouped["GameObjectPosX"].transform("first")
            z0 = grouped["GameObjectPosZ"].transform("first")
            x1 = grouped["GameObjectPosX"].transform("last")
            z1 = grouped["GameObjectPosZ"].transform("last")
            displacement = np.hypot(x1 - x0, z1 - z0)
            cur = cur[displacement >= float(min_disp)]
        rows.append(_filter_stage_row(
            "Min displacement", before, cur, active=bool(min_disp is not None and min_disp > 0),
            note=f"Keeps trials with displacement >= {float(min_disp):g}." if min_disp is not None and min_disp > 0 else None,
            counts_cache=counts_cache))
    
        before = cur
        if trim is not None and trim > 0 and len(cur):
            grouped = cur.groupby("_seg_id", sort=False)
            pos = grouped.cumcount()
            size = grouped["_seg_id"].transform("size")
            trim_n = int(trim)
            cur = cur[(pos >= trim_n) & (pos < size - trim_n)]
        rows.append(_filter_stage_row(
            "Edge trim", before, cur, active=bool(trim is not None and trim > 0),
            note=f"Removes {int(trim)} samples from both ends of each trial." if trim is not None and trim > 0 else None,
            counts_cache=counts_cache))
    
        reach = float(roi_reach) if roi_reach else 3.0
        if pattern:
            roi_base = cur
            roi_filters_on = _on(roi_entered) or _on(roi_trim)
            if roi_filters_on:
                _table, entered_ids, trim_keep, _rois = _roi_masks(
                    roi_base, pattern, reach)
                trim_series = pd.Series(trim_keep, index=roi_base.index)
            else:
                entered_ids = set()
                trim_series = None
            before = cur
            if _on(roi_entered) and len(cur):
                cur = cur[cur["_seg_id"].isin(entered_ids)]
            rows.append(_filter_stage_row(
                "ROI entered only", before, cur, active=_on(roi_entered),
                note="Keeps whole trials that enter any left/right target ROI.",
                counts_cache=counts_cache))
    
            before = cur
            if _on(roi_trim) and len(cur) and trim_series is not None:
                cur = cur[trim_series.loc[cur.index].to_numpy()]
            rows.append(_filter_stage_row(
                "Trim after ROI exit", before, cur, active=_on(roi_trim),
                note="Keeps the approach and first post-entry exit; drops later tail samples.",
                counts_cache=counts_cache))
    
        return rows
    
    
    @app.callback(
        Output("exclusion-info", "children", allow_duplicate=True),
        Output("filter-detail", "children"),
        Input("view-render-state", "data"),
        State("store-glob", "data"),
        State("roi-entered", "value"),
        State("roi-trim", "value"),
        State("roi-reach", "value"),
        State("vel-threshold", "value"),
        State("min-disp", "value"),
        State("trim-samples", "value"),
        State("jump-buffer", "value"),
        State("filter-configs", "value"),
        State("filter-vrs", "value"),
        State("filter-flyids", "value"),
        State("filter-scenes", "value"),
        State("filter-folders", "value"),
        State("trial-min", "value"),
        State("trial-max", "value"),
        State("step-min", "value"),
        State("step-max", "value"),
        State("vel-range-effective", "data"),
        State("disp-range", "value"),
        State("walk-range", "value"),
        prevent_initial_call=True,
    )
    def update_filter_summary(render_state, pattern, roi_entered, roi_trim, roi_reach,
                              vel_thresh, min_disp, trim, jump_buf,
                              cfg, vrs, fids, scenes, folders, trial_min, trial_max,
                              step_min, step_max, vel_sel, disp_sel, walk_sel):
        if not pattern or not render_state:
            return no_update, no_update
        df_all, _, _ = _load_data(pattern)
        df_f, df_sub, _ = _filtered_df(pattern, vel_thresh, min_disp, trim, jump_buf,
                                       cfg, vrs, fids, scenes, folders, trial_min, trial_max,
                                       step_min, step_max, vel_sel, disp_sel,
                                       walk_sel)
        if df_all is None or df_f is None or len(df_f) == 0:
            return "", _filter_detail_children(df_all, vel_thresh, min_disp, trim,
                                               jump_buf, cfg, vrs, fids, scenes,
                                               folders, vel_sel, disp_sel, walk_sel,
                                               trial_min, trial_max, step_min, step_max,
                                               pattern, roi_reach, roi_entered,
                                               roi_trim)
        reach = float(roi_reach) if roi_reach else 3.0
        df_view, _ = (
            _roi_apply(df_f, pattern, reach, _on(roi_entered), _on(roi_trim))
            if _on(roi_entered) or _on(roi_trim) else (df_f, None)
        )
        return (
            _retention_summary(df_all, df_view),
            _filter_detail_children(df_all, vel_thresh, min_disp, trim, jump_buf,
                                    cfg, vrs, fids, scenes, folders,
                                    vel_sel, disp_sel, walk_sel,
                                    trial_min, trial_max,
                                    step_min, step_max,
                                    pattern, roi_reach, roi_entered, roi_trim),
        )
    
    
    _FILTER_CACHE: dict = {}        # signature -> (df_f, df_sub, optional stats_sub)
    _FILTER_CACHE_ORDER: list = []
    _FILTER_CACHE_MAX = 4
    def _filter_signature(pattern, vel_thresh, min_disp, trim, jump_buf,
                          cfg, vrs, fids, scenes, folders, trial_min, trial_max,
                          step_min, step_max, vel_selection, disp_selection,
                          walk_selection):
        def rng(sel):
            return _selected_range(sel)
        def lst(v):
            return tuple(sorted(v)) if v else None
        pkey = _pattern_key(pattern)
        return (pkey, _DATA_TOKEN_BY_PATTERN.get(pkey),
                vel_thresh, min_disp, trim, round(_jump_buffer_seconds(jump_buf), 6),
                lst(cfg), lst(vrs), lst(fids), lst(scenes), lst(folders),
                _trial_range(trial_min, trial_max),
                _value_range(step_min, step_max),
                rng(vel_selection), rng(disp_selection), rng(walk_selection))
    
    
    def _filtered_df_locked(pattern, vel_thresh, min_disp, trim, jump_buf,
                            cfg, vrs, fids, scenes, folders, trial_min, trial_max,
                            step_min, step_max, vel_selection, disp_selection,
                            walk_selection,
                            need_stats=False):
        """
        Shared filtering pipeline (cached). Returns (df_f, df_sub, stats_sub|None).
    
        Caching makes heatmap-only changes (lin/log, metric, bins, percentile)
        cheap — they reuse the already-filtered frame instead of re-running the
        full velocity/displacement/trim pipeline. Segment stats are optional because
        most plot views only need rows; export can upgrade a cached result on demand.
        """
        df, stats, _ = _load_data(pattern)
        if df is None or len(df) == 0:
            return None, None, None
        sig = _filter_signature(pattern, vel_thresh, min_disp, trim, jump_buf,
                                cfg, vrs, fids, scenes, folders, trial_min, trial_max,
                                step_min, step_max, vel_selection, disp_selection,
                                walk_selection)
        if sig in _FILTER_CACHE:
            result = _FILTER_CACHE[sig]
            if need_stats and result[2] is None and result[1] is not None:
                result = (result[0], result[1], compute_segment_stats(result[1]))
                _FILTER_CACHE[sig] = result
            return result
    
        vel_rng = _active_stat_range(vel_selection, stats, "peak_velocity")
        disp_rng = _active_stat_range(_selected_range(disp_selection), stats, "displacement")
        walk_rng = _active_stat_range(
            _selected_range(walk_selection), stats, "distance_walked")
    
        spec = td_grouping.FilterSpec(
            vel_threshold=vel_thresh,
            min_displacement=min_disp,
            edge_trim_samples=trim or 0,
            jump_buffer_ms=jump_buf,
            configs=tuple(cfg) if cfg else None,
            vrs=tuple(vrs) if vrs else None,
            fly_ids=tuple(fids) if fids else None,
            scenes=tuple(scenes) if scenes else None,
            folders=tuple(folders) if folders else None,
            trial_range=_trial_range(trial_min, trial_max),
            step_range=_value_range(step_min, step_max),
            velocity_range=vel_rng,
            displacement_range=disp_rng,
            distance_walked_range=walk_rng,
        )
        filtered = td_grouping.filter_frame(df, spec, stats, compute_stats=need_stats)
        result = (filtered.filtered, filtered.subset, filtered.stats)
        if result[0] is not None:
            result[0].attrs["_frame_token"] = ("filtered", sig, int(len(result[0])))
        if result[1] is not None:
            result[1].attrs["_frame_token"] = ("subset", sig, int(len(result[1])))
    
        _FILTER_CACHE[sig] = result
        _FILTER_CACHE_ORDER.append(sig)
        if len(_FILTER_CACHE_ORDER) > _FILTER_CACHE_MAX:
            old = _FILTER_CACHE_ORDER.pop(0)
            _FILTER_CACHE.pop(old, None)
        return result
    
    
    def _filtered_df(pattern, vel_thresh, min_disp, trim, jump_buf,
                     cfg, vrs, fids, scenes, folders, trial_min, trial_max,
                     step_min, step_max, vel_selection, disp_selection,
                     walk_selection,
                     need_stats=False):
        """Single-flight wrapper around the shared vectorised filter pipeline."""
        with _FILTER_LOCK:
            return _filtered_df_locked(
                pattern, vel_thresh, min_disp, trim, jump_buf,
                cfg, vrs, fids, scenes, folders, trial_min, trial_max,
                step_min, step_max, vel_selection, disp_selection,
                walk_selection,
                need_stats=need_stats)
    
    
    def _apply_viewport(fig, viewport, df, max_span_mult=3.0):
        """Re-apply a stored shared viewbox to `fig`, but reject garbage ranges.
    
        A scaleanchor plot that fires a relayout while briefly mis-sized can report a
        range many times larger than the data — applying it zooms everything out to
        an empty view. We only honour a stored range whose span is within a generous
        multiple of the data's natural extent; anything wilder is treated as "no
        viewbox" so the figure keeps its own sensible autoscale.
        """
        if not viewport or viewport.get("reset") or df is None or len(df) == 0:
            return
        try:
            rx, rz = _shared_range(df)
        except Exception:
            rx = rz = None
    
        def _ok(rng, natural):
            if not rng or len(rng) != 2 or rng[0] is None or rng[1] is None:
                return False
            if natural is None:
                return True
            span = abs(rng[1] - rng[0])
            nat = abs(natural[1] - natural[0]) or 1.0
            return span <= nat * float(max_span_mult)
    
        if _ok(viewport.get("xaxis"), rx):
            fig.update_xaxes(range=viewport["xaxis"])
        if _ok(viewport.get("yaxis"), rz):
            fig.update_yaxes(range=viewport["yaxis"])
    
    
    def _apply_viewport_to_current_range(fig, viewport, max_span_mult=2.0):
        """Apply a viewport only if it is close to the figure's own current range.
    
        Heatmap bounds can be clipped (`hbound`), so validating against the raw data
        extent can accept a stale, much broader viewbox that leaves the actual
        heatmap as a tiny island and makes wheel-zoom feel like it vanished.
        """
        if not viewport or viewport.get("reset"):
            return
    
        def _layout_range(axis_name):
            ax = getattr(fig.layout, axis_name, None)
            rng = getattr(ax, "range", None) if ax is not None else None
            return list(rng) if rng and len(rng) == 2 else None
    
        def _ok(vp_rng, natural):
            if not vp_rng or len(vp_rng) != 2 or natural is None:
                return False
            span = abs(float(vp_rng[1]) - float(vp_rng[0]))
            nat = abs(float(natural[1]) - float(natural[0])) or 1.0
            if span > nat * float(max_span_mult):
                return False
            lo = max(min(vp_rng), min(natural))
            hi = min(max(vp_rng), max(natural))
            return hi > lo
    
        xr = _layout_range("xaxis")
        yr = _layout_range("yaxis")
        meta = getattr(fig.layout, "meta", None)
        spatial_axis_count = (
            int(meta.get("spatial_axis_count", 0))
            if isinstance(meta, dict) else 0
        )
    
        def _set_spatial_ranges(axis_prefix, value):
            if not spatial_axis_count:
                if axis_prefix == "x":
                    fig.update_xaxes(range=value)
                else:
                    fig.update_yaxes(range=value)
                return
            for index in range(1, spatial_axis_count + 1):
                key = f"{axis_prefix}axis{'' if index == 1 else index}"
                fig.update_layout(**{key: dict(range=value)})
    
        if _ok(viewport.get("xaxis"), xr):
            _set_spatial_ranges("x", viewport["xaxis"])
        if _ok(viewport.get("yaxis"), yr):
            _set_spatial_ranges("y", viewport["yaxis"])
    
    
    def _build_spatial_figures(
            *, df_f, df_spatial, pattern, reach, group_by, pool_mode, ncols,
            hm_binsize, bound_pct, hm_metric, hm_scale, hm_cmin, hm_cmax,
            hm_crange, do_rebase, roi_entered, roi_trim, polar_angle_source,
            polar_moving, polar_walk, rois, flow_max_radius, viewport):
        """Build the two views that share spatial aggregation geometry.
    
        Keeping this work in one helper makes the master render and focused
        grid-update callback use identical ROI, binning, colour, direction, and
        viewport semantics.  The returned timings let callers report the actual
        cost without mixing it into unrelated ROI or distribution stages.
        """
        started = time.perf_counter()
        heat_fig, heat_variants, heat_color_distributions = (
            build_heatmap_mask_variants(
                df_f, pattern, reach, group_by, pool_mode, ncols,
                bin_size=hm_binsize, bound_pct=bound_pct,
                cmin=hm_cmin, cmax=hm_cmax, crange_mode=hm_crange,
                do_rebase=do_rebase, entered_only=_on(roi_entered),
                trim_tail=_on(roi_trim), max_points=None,
                metric=hm_metric or "time", log_scale=(hm_scale == "log"),
            )
        )
        _apply_viewport_to_current_range(heat_fig, viewport, max_span_mult=1.5)
        heat_seconds = time.perf_counter() - started
    
        started = time.perf_counter()
        flow_fig = build_direction_field_figure(
            df_spatial, group_by, pool_mode, ncols=ncols,
            bin_size=hm_binsize, bound_pct=bound_pct,
            metric=hm_metric or "time", log_scale=(hm_scale == "log"),
            cmin=hm_cmin, cmax=hm_cmax, crange_mode=hm_crange,
            angle_source=polar_angle_source, moving_only=_on(polar_moving),
            walk_thresh=polar_walk, rois=rois, reach_radius=reach,
            show_rois=bool(rois) and not do_rebase,
            max_radius=flow_max_radius,
        )
        _apply_viewport_to_current_range(flow_fig, viewport, max_span_mult=1.5)
        flow_seconds = time.perf_counter() - started
        return (
            heat_fig,
            heat_variants,
            heat_color_distributions,
            flow_fig,
            {"heatmap": heat_seconds, "flow field": flow_seconds},
        )
    
    
    @app.callback(
        Output("trajectory-plot", "figure"),
        Output("heatmap-figure-store", "data", allow_duplicate=True),
        Output("heatmap-variants", "data", allow_duplicate=True),
        Output("heatmap-color-distributions", "data", allow_duplicate=True),
        Output("flow-figure-store", "data", allow_duplicate=True),
        Output("roi-plot", "figure", allow_duplicate=True),
        Output("custom-region-diagnostics-plot", "figure", allow_duplicate=True),
        Output("custom-region-stats-store", "data", allow_duplicate=True),
        Output("polar-plot", "figure", allow_duplicate=True),
        Output("trial-metrics-plot", "figure"),
        Output("raw-trace-plot", "figure"),
        Output("raw-trace-wrap", "style"),
        Output("data-summary", "children"),
        Output("exclusion-info", "children"),
        Output("view-render-state", "data", allow_duplicate=True),
        Output("plot-status", "children", allow_duplicate=True),
        Input("btn-plot", "n_clicks"),
        State("data-generation", "data"),
        State("store-glob", "data"),
        State("vel-threshold", "value"),
        State("min-disp", "value"),
        State("trim-samples", "value"),
        State("jump-buffer", "value"),
        State("group-by", "value"),
        State("pool-mode", "value"),
        State("color-by", "value"),
        State("animate-toggle", "value"),
        State("rebase-origin", "value"),
        State("heatmap-binsize", "value"),
        State("heatmap-scale", "value"),
        State("heatmap-bound", "value"),
        State("heatmap-metric", "value"),
        State("heatmap-cmin", "value"),
        State("heatmap-cmax", "value"),
        State("heatmap-crange", "value"),
        State("filter-configs", "value"),
        State("filter-vrs", "value"),
        State("filter-flyids", "value"),
        State("filter-scenes", "value"),
        State("filter-folders", "value"),
        State("trial-min", "value"),
        State("trial-max", "value"),
        State("step-min", "value"),
        State("step-max", "value"),
        State("raw-columns", "value"),
        State("subplot-ncols", "value"),
        State("plot-points", "value"),
        State("traj-trial-fraction", "value"),
        State("btn-traj-resample", "n_clicks"),
        State("render-mode", "value"),
        State("vel-range-effective", "data"),
        State("disp-range", "value"),
        State("walk-range", "value"),
        State("viewport-store", "data"),
        State("roi-show", "value"),
        State("roi-reach", "value"),
        State("roi-trim", "value"),
        State("roi-entered", "value"),
        State("polar-moving", "value"),
        State("polar-walk", "value"),
        State("polar-angle-source", "value"),
        State("polar-r-range", "value"),
        State("polar-min-point-frac", "value"),
        State("polar-min-animal-frac", "value"),
        State("flow-max-radius", "value"),
        State("custom-region-enabled", "value"),
        State("custom-regions-store", "data"),
        State("distribution-mode", "value"),
        State("distribution-show-points", "value"),
        State("stats-unit", "value"),
        State("spatial-unit-scale", "value"),
        State("spatial-unit-label", "value"),
        prevent_initial_call=True,
    )
    def update_plots(n, generation, pattern, vel_thresh, min_disp, trim, jump_buf,
                     group_by, pool_mode, color_by, animate, rebase, hm_binsize, hm_scale,
                     hm_bound, hm_metric, hm_cmin, hm_cmax, hm_crange, cfg, vrs, fids,
                     scenes, folders, trial_min, trial_max, step_min, step_max,
                     raw_cols, ncols, max_points, traj_fraction, traj_sample_seed,
                     render_mode, vel_selection, disp_selection, walk_selection,
                     viewport, roi_show, roi_reach,
                     roi_trim, roi_entered, polar_moving, polar_walk, polar_angle_source,
                     polar_r_range,
                     polar_min_point_frac, polar_min_animal_frac,
                     flow_max_radius, custom_region_enabled, custom_regions,
                     distribution_mode, distribution_show_points, stats_unit,
                     spatial_unit_scale, spatial_unit_label):
        empty = go.Figure().update_layout(height=400, template="plotly_white")
        raw_hidden = {"display": "none"}
        if not pattern:
            return (empty, empty, {}, {}, empty, empty, empty, {}, empty, empty,
                    empty, raw_hidden,
                    "Choose a data folder or CSV glob to begin.",
                    "", {}, "Waiting for data.")
    
        op_id = _progress_begin(
            "render",
            ["Filter/cache", "Trajectories", "Raw traces"],
            "Applying filters from the retained in-memory dataset…",
        )
        started = time.perf_counter()
        stage_started = started
        timings = {}
        mode = _render_mode(render_mode)
        LOGGER.info(
            "render.start epoch=%s mode=%s group=%s pool=%s source=%r",
            int(n or 0), mode, group_by, pool_mode, pattern,
        )
        # Fresh layouts briefly expose [0, 1] slider placeholders before the real
        # dataset bounds arrive. A load-generation render must never interpret
        # those placeholders as intentional filters.
        if isinstance(generation, dict) and generation.get("reset_controls"):
            # A source change updates six range controls and their exact boxes in
            # parallel.  The load-barrier click is ordered after the slider
            # response, but the derived effective-range Store can still carry
            # the prior dataset for one browser turn.  The first render of a new
            # source must therefore be analytically neutral; later user edits
            # arrive through the normal debounced filter path.
            vel_selection = None
            disp_selection = None
            walk_selection = None
        elif generation:
            if (_numeric_range(vel_selection) == (0.0, 1.0)
                    and not (isinstance(vel_selection, dict)
                             and vel_selection.get("explicit"))):
                vel_selection = None
            if _numeric_range(disp_selection) == (0.0, 1.0):
                disp_selection = None
            if _numeric_range(walk_selection) == (0.0, 1.0):
                walk_selection = None
        df_f, df_sub, _stats_sub = _filtered_df(
            pattern, vel_thresh, min_disp, trim, jump_buf,
            cfg, vrs, fids, scenes, folders, trial_min, trial_max,
            step_min, step_max, vel_selection, disp_selection, walk_selection)
        timings["filter/cache"] = time.perf_counter() - stage_started
        if df_sub is None:
            msg = "No CSV rows matched the current data source."
            _progress_finish(op_id, msg, failed=True)
            LOGGER.warning("render.empty epoch=%s reason=no_rows source=%r", n, pattern)
            return (empty, empty, {}, {}, empty, empty, empty, {}, empty, empty, empty,
                    raw_hidden, msg,
                    "", {"epoch": int(n or 0)}, msg)
        if len(df_sub) == 0:
            msg = "No trajectories match the active filters."
            _progress_finish(op_id, msg, failed=True)
            LOGGER.warning("render.empty epoch=%s reason=filters source=%r", n, pattern)
            return (empty, empty, {}, {}, empty, empty, empty, {}, empty, empty, empty,
                    raw_hidden, msg,
                    "", {"epoch": int(n or 0)}, msg)
    
        df, _native_stats, metas = _load_data(pattern)
        do_animate = bool(animate) and "on" in (animate or [])
        do_rebase = bool(rebase) and "on" in (rebase or [])
    
        # Only target-dependent filters may delay the core trajectory. Merely
        # enabling target diagnostics is handled by the later target stage.
        stage_started = time.perf_counter()
        rois = rois_by_config(metas)
        reach = float(roi_reach) if roi_reach else 3.0
        needs_roi_subset = _on(roi_entered) or _on(roi_trim)
        df_view, _table = (
            _roi_apply(df_f, pattern, reach, _on(roi_entered), _on(roi_trim))
            if needs_roi_subset else (df_f, None)
        )
        ncols_val = _resolve_panel_columns(ncols, df_view, group_by, pool_mode)
        exclusion = _retention_summary(df, df_view)
        roi_outcomes = (
            roi_outcome_by_segment(df_view, rois, reach)
            if color_by == "roi" and rois else None
        )
        timings["target-dependent filtering"] = time.perf_counter() - stage_started
        _progress_stage(
            op_id, 1, done=0, total=1,
            message=f"Building merged WebGL trajectories from {len(df_view):,} visible rows…",
        )
    
        stage_started = time.perf_counter()
        # Keep the complete filtered display frame mounted. The displayed-trial
        # fraction is applied browser-side by `_seg_id`, so changing it never
        # rebuilds analytical sections or Plotly figures.
        df_display = df_view
        df_plot = rebase_to_origin(df_display) if do_rebase else df_display
        # Spatial-field extent is a binning control, not a trajectory redraw
        # control.  Trajectory startup keeps a stable robust fit while heatmap
        # bound changes use the focused spatial callback below.
        shared_fit = _robust_range(df_plot, 98.0) if len(df_plot) else None
        df_plot = mask_stationary_trajectory_points(
            df_plot, _on(polar_moving), polar_walk)
        traj_budget = _budget(BUDGET_SVG if do_animate else BUDGET_GL,
                              BUDGET_SVG_SPEED if do_animate else BUDGET_GL_SPEED,
                              mode, max_points)
        df_traj_sample = df_plot
        df_plot_draw = (
            _decimate_frame(df_traj_sample, traj_budget)
            if mode == "speed" else df_traj_sample
        )
        traj_max_points = len(df_plot_draw) if mode == "speed" else max_points
        traj_fig = build_trajectory_figure(
            df_plot_draw, group_by, pool_mode, ncols=ncols_val,
            color_by=color_by or "categorical", animate=do_animate,
            max_points=traj_max_points, rois=None, reach_radius=reach,
            show_rois=False, roi_counts=None,
            roi_outcomes=roi_outcomes, view_range=shared_fit)
        _apply_viewport(traj_fig, viewport, df_plot_draw)
        timings["trajectory"] = time.perf_counter() - stage_started
        # Downstream figures intentionally remain mounted at their previous
        # values. Their focused stages replace them only after the new figure is
        # complete, which makes one-setting comparisons visually continuous.
        _progress_stage(
            op_id, 2, done=0, total=1,
            message="Finalizing optional raw traces and dashboard state…",
        )
    
        stage_started = time.perf_counter()
        raw_style = {"display": "block"} if raw_cols else raw_hidden
        raw_fig = (build_raw_trace_figure(
            df_view, raw_cols or [],
            max_points=_budget(BUDGET_RAW, BUDGET_RAW_SPEED, mode, max_points))
            if raw_cols else empty)
        timings["raw traces"] = time.perf_counter() - stage_started
    
        drawn = sum(len(t.x) for t in traj_fig.data
                    if getattr(t, "x", None) is not None)
        n_frames = len(traj_fig.frames)
        n_traces = int(df_view["_seg_id"].nunique()) if len(df_view) else 0
        n_displayed_traces = (
            max(1, int(math.ceil(
                _trial_display_fraction(traj_fraction) * n_traces)))
            if n_traces else 0
        )
        n_segs_before = df_sub["_seg_id"].nunique()
        bt = time.perf_counter() - started
        timings["total"] = bt
        summary = (f"{_compact_count(len(df_view))}/{_compact_count(len(df_sub))} pts | "
                   f"{_compact_count(n_traces)}/{_compact_count(n_segs_before)} segs | "
                   f"{_compact_count(n_displayed_traces)}/{_compact_count(n_traces)} "
                   f"displayed trials | "
                   f"trajectory ~{drawn:,}/{traj_budget:,} drawn pts | "
                   f"{n_frames} frames | trajectory ready {bt:.2f}s | colour: {color_by}")
        if mode == "speed":
            summary += " | Speed mode"
    
        LOGGER.info(
            "render.done epoch=%s mode=%s input_rows=%d visible_rows=%d "
            "segments=%d displayed_segments=%d drawn_points=%d seconds=%.3f",
            int(n or 0), mode, len(df_sub), len(df_view), n_traces,
            n_displayed_traces, drawn, bt,
        )
    
        render_state = {
            "epoch": int(n or 0), "data": _DATA_TOKEN_BY_PATTERN.get(_pattern_key(pattern)),
            "mode": mode, "completed": time.time(),
            "timings": {k: round(float(v), 4) for k, v in timings.items()},
            "operation": "trajectory",
            "group_by": str(group_by or "config"),
            "pool_mode": str(pool_mode or "separate"),
            "groups": list(_group_frames(
                df_view, group_by, pool_mode, ncols_val).keys()),
        }
        _progress_finish(op_id, f"Trajectory ready in {bt:.2f}s; loading occupancy…")
        return (traj_fig, no_update, no_update,
                no_update, no_update, no_update,
                no_update, no_update, no_update,
                no_update, raw_fig, raw_style, summary, exclusion,
                render_state, f"Trajectory ready in {bt:.2f}s; loading occupancy…")
    
    
    @app.callback(
        Output("heatmap-figure-store", "data", allow_duplicate=True),
        Output("heatmap-variants", "data", allow_duplicate=True),
        Output("heatmap-color-distributions", "data", allow_duplicate=True),
        Output("flow-figure-store", "data", allow_duplicate=True),
        Output("spatial-render-state", "data"),
        Output("plot-status", "children", allow_duplicate=True),
        Input("heatmap-binsize", "value"),
        Input("heatmap-bound", "value"),
        Input("view-render-state", "data"),
        State("store-glob", "data"),
        State("vel-threshold", "value"),
        State("min-disp", "value"),
        State("trim-samples", "value"),
        State("jump-buffer", "value"),
        State("filter-configs", "value"),
        State("filter-vrs", "value"),
        State("filter-flyids", "value"),
        State("filter-scenes", "value"),
        State("filter-folders", "value"),
        State("trial-min", "value"),
        State("trial-max", "value"),
        State("step-min", "value"),
        State("step-max", "value"),
        State("vel-range-effective", "data"),
        State("disp-range", "value"),
        State("walk-range", "value"),
        State("group-by", "value"),
        State("pool-mode", "value"),
        State("subplot-ncols", "value"),
        State("rebase-origin", "value"),
        State("heatmap-metric", "value"),
        State("heatmap-scale", "value"),
        State("heatmap-cmin", "value"),
        State("heatmap-cmax", "value"),
        State("heatmap-crange", "value"),
        State("roi-reach", "value"),
        State("roi-entered", "value"),
        State("roi-trim", "value"),
        State("polar-angle-source", "value"),
        State("polar-moving", "value"),
        State("polar-walk", "value"),
        State("flow-max-radius", "value"),
        State("viewport-store", "data"),
        prevent_initial_call=True,
    )
    def update_spatial_grid(
            hm_binsize, hm_bound, render_state, pattern, vel_thresh, min_disp,
            trim, jump_buf, cfg, vrs, fids, scenes, folders, trial_min,
            trial_max, step_min, step_max, vel_selection, disp_selection,
            walk_selection,
            group_by, pool_mode, ncols, rebase, hm_metric, hm_scale, hm_cmin,
            hm_cmax, hm_crange, roi_reach, roi_entered, roi_trim,
            polar_angle_source, polar_moving, polar_walk, flow_max_radius,
            viewport):
        """Stage 2: build occupancy without rebuilding trajectories or Gandiva."""
        data_token = (
            _DATA_TOKEN_BY_PATTERN.get(_pattern_key(pattern))
            if pattern else None
        )
        token_matches = (
            json.dumps(render_state.get("data"), default=str, sort_keys=True)
            == json.dumps(data_token, default=str, sort_keys=True)
            if render_state else False
        )
        if (not pattern or not render_state or not render_state.get("completed")
                or not token_matches):
            return (no_update,) * 6
    
        trigger = str(ctx.triggered_id or "spatial grid")
        op_id = _progress_begin(
            "spatial-grid",
            ["Filter/cache", "Occupancy bins"],
            "Building occupancy from cached filtered rows…",
        )
        started = time.perf_counter()
        df_f, _, _ = _filtered_df(
            pattern, vel_thresh, min_disp, trim, jump_buf,
            cfg, vrs, fids, scenes, folders, trial_min, trial_max,
            step_min, step_max, vel_selection, disp_selection, walk_selection,
        )
        if df_f is None or len(df_f) == 0:
            _progress_finish(
                op_id, "Spatial update skipped — no rows match the filters.",
                failed=True)
            return (no_update,) * 5 + (
                "Spatial update skipped — no rows match the filters.",)
    
        _progress_stage(
            op_id, 1, done=0, total=1,
            message="Re-binning occupancy without touching other analyses…",
        )
        reach = float(roi_reach or 3.0)
        df_view, _ = (
            _roi_apply(df_f, pattern, reach, _on(roi_entered), _on(roi_trim))
            if _on(roi_entered) or _on(roi_trim) else (df_f, None)
        )
        do_rebase = _on(rebase)
        df_spatial = rebase_to_origin(df_view) if do_rebase else df_view
        ncols_value = _resolve_panel_columns(
            ncols, df_spatial, group_by, pool_mode)
        bound_pct = float(hm_bound) if hm_bound not in (None, "") else 98.0
        heat_started = time.perf_counter()
        heat, variants, distributions = build_heatmap_mask_variants(
            df_f, pattern, reach, group_by, pool_mode, ncols_value,
            bin_size=hm_binsize, bound_pct=bound_pct,
            cmin=hm_cmin, cmax=hm_cmax, crange_mode=hm_crange,
            do_rebase=do_rebase, entered_only=_on(roi_entered),
            trim_tail=_on(roi_trim), max_points=None,
            metric=hm_metric or "time", log_scale=(hm_scale == "log"),
            include_rois=False,
        )
        _apply_viewport_to_current_range(heat, viewport, max_span_mult=1.5)
        heat_seconds = time.perf_counter() - heat_started
        elapsed = time.perf_counter() - started
        message = (
            f"Occupancy ready after {trigger.replace('-', ' ')} in "
            f"{elapsed:.2f}s; loading polar…"
        )
        _progress_finish(op_id, message)
        LOGGER.info(
            "spatial.done trigger=%s rows=%d panels=%d seconds=%.3f",
            trigger, len(df_spatial),
            len(_group_frames(df_spatial, group_by, pool_mode, ncols_value)),
            elapsed,
        )
        spatial_state = {
            "completed": time.time(), "data": data_token,
            "epoch": int((render_state or {}).get("epoch", 0)),
            "trigger": trigger, "bin_size": hm_binsize,
            "bound_pct": bound_pct, "seconds": round(elapsed, 4),
        }
        return (
            heat.to_plotly_json(), variants, distributions,
            no_update, spatial_state, message,
        )
    
    
    @app.callback(
        Output("transition-data-store", "data"),
        Input("targets-render-state", "data"),
        Input("transition-enabled", "value"),
        Input("transition-split-z", "value"),
        Input("transition-min-trials", "value"),
        State("spatial-render-state", "data"),
        State("store-glob", "data"),
        State("vel-threshold", "value"),
        State("min-disp", "value"),
        State("trim-samples", "value"),
        State("jump-buffer", "value"),
        State("filter-configs", "value"),
        State("filter-vrs", "value"),
        State("filter-flyids", "value"),
        State("filter-scenes", "value"),
        State("filter-folders", "value"),
        State("trial-min", "value"),
        State("trial-max", "value"),
        State("step-min", "value"),
        State("step-max", "value"),
        State("vel-range-effective", "data"),
        State("disp-range", "value"),
        State("walk-range", "value"),
        State("group-by", "value"),
        State("pool-mode", "value"),
        State("subplot-ncols", "value"),
        State("heatmap-binsize", "value"),
        State("heatmap-bound", "value"),
        State("rebase-origin", "value"),
        State("roi-reach", "value"),
        State("roi-entered", "value"),
        State("roi-trim", "value"),
        State("viewport-store", "data"),
        prevent_initial_call=True,
    )
    def update_transition_probability(
            render_state, enabled, split_z, min_trials, spatial_state, pattern,
            vel_thresh, min_disp, trim, jump_buf, cfg, vrs, fids, scenes,
            folders, trial_min, trial_max, step_min, step_max, vel_selection,
            disp_selection, walk_selection, group_by, pool_mode, ncols,
            hm_binsize, hm_bound,
            rebase, roi_reach, roi_entered, roi_trim, viewport):
        """Build only the optional transition grid; never arm the master renderer."""
        if (ctx.triggered_id == "targets-render-state"
                and (render_state or {}).get("completed")
                and not (render_state or {}).get("transition_relevant", True)):
            return no_update
        if not _on(enabled):
            return {
                "enabled": False,
                "message": (
                    "Transition observer off. Enable it to "
                    "calculate conditional trial probabilities."
                ),
            }
        if not pattern or not render_state or not render_state.get("completed"):
            return {
                "enabled": True,
                "message": "Load and render data before calculating transitions.",
            }
        started = time.perf_counter()
        try:
            df_f, _df_sub, _stats = _filtered_df(
                pattern, vel_thresh, min_disp, trim, jump_buf,
                cfg, vrs, fids, scenes, folders, trial_min, trial_max,
                step_min, step_max, vel_selection, disp_selection,
                walk_selection)
            if df_f is None or len(df_f) == 0:
                return {
                    "enabled": True,
                    "message": "No filtered trials are available for transitions.",
                }
            reach = float(roi_reach or 3.0)
            df_view, _table = (
                _roi_apply(
                    df_f, pattern, reach, _on(roi_entered), _on(roi_trim))
                if _on(roi_entered) or _on(roi_trim) else (df_f, None)
            )
            df_spatial = rebase_to_origin(df_view) if _on(rebase) else df_view
            ncols_value = _resolve_panel_columns(
                ncols, df_spatial, group_by, pool_mode)
            bound_pct = (
                float(hm_bound) if hm_bound not in (None, "") else 98.0)
            minimum = max(1, int(min_trials or 1))
            split_key = (
                None if split_z in (None, "") else round(float(split_z), 10))
            cache_key = (
                _frame_cache_token(df_spatial), str(group_by or "config"),
                str(pool_mode or "separate"), ncols_value,
                None if hm_binsize in (None, "") else round(float(hm_binsize), 10),
                round(bound_pct, 8), split_key, minimum,
                json.dumps(
                    _VISUAL_STYLE.get("transition", {}),
                    sort_keys=True, separators=(",", ":")),
            )
            bundle = _TRANSITION_CACHE.get(cache_key)
            if bundle is None:
                bundle = build_transition_probability_bundle(
                    df_spatial, group_by=group_by, pool_mode=pool_mode,
                    ncols=ncols_value, bin_size=hm_binsize,
                    bound_pct=bound_pct, split_z=split_z,
                    min_trials=minimum, outcome="crossed")
                _TRANSITION_CACHE[cache_key] = bundle
                _TRANSITION_CACHE_ORDER.append(cache_key)
                while len(_TRANSITION_CACHE_ORDER) > _TRANSITION_CACHE_MAX:
                    old = _TRANSITION_CACHE_ORDER.pop(0)
                    _TRANSITION_CACHE.pop(old, None)
            else:
                try:
                    _TRANSITION_CACHE_ORDER.remove(cache_key)
                except ValueError:
                    pass
                _TRANSITION_CACHE_ORDER.append(cache_key)
            figure = go.Figure(bundle.get("figure") or {})
            _apply_viewport_to_current_range(
                figure, viewport, max_span_mult=1.5)
            output = dict(bundle)
            output["figure"] = figure.to_plotly_json()
            output["seconds"] = round(time.perf_counter() - started, 4)
            output["message"] = (
                f"{bundle.get('message', 'Transition grid ready')} "
                f"Built in {output['seconds']:.2f}s."
            )
            return output
        except Exception as exc:
            LOGGER.exception("transition.failed")
            return {
                "enabled": True,
                "message": (
                    f"Transition calculation failed: "
                    f"{type(exc).__name__}: {exc}"
                ),
                "error": True,
            }
    
    
    @app.callback(
        Output("trajectory-plot", "figure", allow_duplicate=True),
        Output("plot-status", "children", allow_duplicate=True),
        Output("data-summary", "children", allow_duplicate=True),
        Input("color-by", "value"),
        Input("render-mode", "value"),
        Input("animate-toggle", "value"),
        Input("plot-points", "value"),
        Input("polar-moving", "value"),
        Input("polar-walk", "value"),
        State("view-render-state", "data"),
        State("store-glob", "data"),
        State("vel-threshold", "value"),
        State("min-disp", "value"),
        State("trim-samples", "value"),
        State("jump-buffer", "value"),
        State("filter-configs", "value"),
        State("filter-vrs", "value"),
        State("filter-flyids", "value"),
        State("filter-scenes", "value"),
        State("filter-folders", "value"),
        State("trial-min", "value"),
        State("trial-max", "value"),
        State("step-min", "value"),
        State("step-max", "value"),
        State("vel-range-effective", "data"),
        State("disp-range", "value"),
        State("walk-range", "value"),
        State("group-by", "value"),
        State("pool-mode", "value"),
        State("subplot-ncols", "value"),
        State("rebase-origin", "value"),
        State("viewport-store", "data"),
        State("roi-show", "value"),
        State("roi-reach", "value"),
        State("roi-entered", "value"),
        State("roi-trim", "value"),
        State("polar-angle-source", "value"),
        State("polar-r-range", "value"),
        State("polar-min-point-frac", "value"),
        State("polar-min-animal-frac", "value"),
        State("custom-region-enabled", "value"),
        State("custom-regions-store", "data"),
        State("stats-unit", "value"),
        State("data-summary", "children"),
        prevent_initial_call=True,
    )
    def update_colour_views(
            color_by, render_mode, animate, max_points, polar_moving,
            polar_walk, render_state, pattern,
            vel_thresh, min_disp, trim, jump_buf,
            cfg, vrs, fids, scenes, folders, trial_min, trial_max, step_min,
            step_max, vel_selection, disp_selection, walk_selection,
            group_by, pool_mode, ncols,
            rebase, viewport, roi_show,
            roi_reach, roi_entered, roi_trim, polar_angle_source,
            polar_r_range, polar_min_point_frac,
            polar_min_animal_frac, custom_region_enabled, custom_regions,
            stats_unit, current_summary):
        """Refresh only the trajectory drawing payload and encoding."""
        if not pattern or not render_state:
            return no_update, no_update, no_update
        trigger = str(ctx.triggered_id or "drawing control")
        op_id = _progress_begin(
            "drawing",
            ["Filter/cache", "Trajectory"],
            "Updating the trajectory drawing payload…",
        )
        started = time.perf_counter()
        df_f, _, _ = _filtered_df(
            pattern, vel_thresh, min_disp, trim, jump_buf,
            cfg, vrs, fids, scenes, folders, trial_min, trial_max,
            step_min, step_max, vel_selection, disp_selection, walk_selection)
        if df_f is None or len(df_f) == 0:
            message = _msg_figure("No trajectories match the active filters.")
            _progress_finish(
                op_id, "Drawing update skipped — no rows match the filters.",
                failed=True,
            )
            return (
                message, "Drawing update skipped — no matching rows.", no_update,
            )
    
        _, _, metas = _load_data(pattern)
        rois = rois_by_config(metas)
        reach = float(roi_reach or 3.0)
        want_rois = bool(rois) and _on(roi_show)
        needs_roi_subset = _on(roi_entered) or _on(roi_trim)
        df_view, table = (
            _roi_apply(df_f, pattern, reach, _on(roi_entered), _on(roi_trim))
            if want_rois or needs_roi_subset or color_by == "roi"
            else (df_f, None)
        )
        ncols_value = _resolve_panel_columns(ncols, df_view, group_by, pool_mode)
        do_rebase = _on(rebase)
        do_animate = _on(animate)
        mode = _render_mode(render_mode)
        draw_frame = rebase_to_origin(df_view) if do_rebase else df_view
        # Occupancy extent is a spatial-grid concern.  Keep trajectory fitting
        # stable when the heatmap bound changes.
        shared_fit = _robust_range(draw_frame, 98.0)
        draw_frame = mask_stationary_trajectory_points(
            draw_frame, _on(polar_moving), polar_walk)
        budget = _budget(
            BUDGET_SVG if do_animate else BUDGET_GL,
            BUDGET_SVG_SPEED if do_animate else BUDGET_GL_SPEED,
            mode, max_points,
        )
        draw_sample = (
            _decimate_frame(draw_frame, budget) if mode == "speed" else draw_frame)
        outcomes = (
            roi_outcome_by_segment(df_view, rois, reach)
            if (color_by == "roi" or want_rois) and rois else None
        )
        _progress_stage(
            op_id, 1, done=0, total=1,
            message="Rebuilding only the trajectory drawing payload…",
        )
        trajectory = build_trajectory_figure(
            draw_sample, group_by, pool_mode, ncols=ncols_value,
            color_by=color_by or "categorical", animate=do_animate,
            max_points=len(draw_sample) if mode == "speed" else max_points,
            rois=rois, reach_radius=reach,
            show_rois=want_rois and not do_rebase, roi_counts=table,
            roi_outcomes=outcomes, view_range=shared_fit,
        )
        _apply_viewport(trajectory, viewport, draw_sample)
        elapsed = time.perf_counter() - started
        updated_summary = no_update
        if trigger == "color-by" and isinstance(current_summary, str):
            updated_summary = re.sub(
                r"colour: [^|]+", f"colour: {color_by} ", current_summary)
        elif trigger == "render-mode" and isinstance(current_summary, str):
            cleaned = re.sub(r"\s*\|\s*(Speed|Accuracy) mode", "", current_summary)
            updated_summary = f"{cleaned} | {mode.title()} mode"
        label = {
            "color-by": f"colour {color_by}",
            "render-mode": f"{mode} drawing mode",
            "animate-toggle": "playback mode",
            "plot-points": "point budget",
            "polar-moving": "moving-only drawing",
            "polar-walk": "moving speed threshold",
        }.get(trigger, trigger.replace("-", " "))
        message = (
            f"Ready — trajectory updated for {label} in {elapsed:.2f}s."
        )
        _progress_finish(op_id, message)
        return (
            trajectory,
            message,
            updated_summary,
        )


    @app.callback(
        Output("trial-metrics-plot", "figure", allow_duplicate=True),
        Output("metrics-render-state", "data"),
        Output("plot-status", "children", allow_duplicate=True),
        Input("polar-render-state", "data"),
        State("store-glob", "data"),
        State("vel-threshold", "value"),
        State("min-disp", "value"),
        State("trim-samples", "value"),
        State("jump-buffer", "value"),
        State("filter-configs", "value"),
        State("filter-vrs", "value"),
        State("filter-flyids", "value"),
        State("filter-scenes", "value"),
        State("filter-folders", "value"),
        State("trial-min", "value"),
        State("trial-max", "value"),
        State("step-min", "value"),
        State("step-max", "value"),
        State("vel-range-effective", "data"),
        State("disp-range", "value"),
        State("walk-range", "value"),
        State("group-by", "value"),
        State("pool-mode", "value"),
        State("roi-reach", "value"),
        State("roi-entered", "value"),
        State("roi-trim", "value"),
        State("distribution-mode", "value"),
        State("distribution-show-points", "value"),
        State("stats-unit", "value"),
        State("spatial-unit-scale", "value"),
        State("spatial-unit-label", "value"),
        prevent_initial_call=True,
    )
    def update_trial_metrics_stage(
            polar_state, pattern, vel_thresh, min_disp, trim, jump_buf,
            cfg, vrs, fids, scenes, folders, trial_min, trial_max, step_min,
            step_max, vel_selection, disp_selection, walk_selection,
            group_by, pool_mode,
            roi_reach, roi_entered, roi_trim, distribution_mode,
            distribution_show_points, stats_unit, spatial_unit_scale,
            spatial_unit_label):
        """Stage 4: render cached per-trial metrics after the polar view."""
        if not pattern or not (polar_state or {}).get("completed"):
            LOGGER.info("metrics.stage_skip reason=not_ready")
            return no_update, no_update, no_update
        if ((polar_state or {}).get("trigger") != "spatial-render-state"
                or (polar_state or {}).get("stage_trigger") != "view-render-state"):
            LOGGER.info(
                "metrics.stage_skip reason=unrelated trigger=%s stage=%s",
                (polar_state or {}).get("trigger"),
                (polar_state or {}).get("stage_trigger"),
            )
            return no_update, no_update, no_update
        LOGGER.info("metrics.stage_start epoch=%s", (polar_state or {}).get("epoch"))
        started = time.perf_counter()
        df_f, _, _ = _filtered_df(
            pattern, vel_thresh, min_disp, trim, jump_buf,
            cfg, vrs, fids, scenes, folders, trial_min, trial_max,
            step_min, step_max, vel_selection, disp_selection, walk_selection)
        if df_f is None or len(df_f) == 0:
            message = "Trial metrics skipped — no matching rows."
            return _msg_figure(message), {
                "completed": time.time(), "error": True,
            }, message
        _, native_stats, _ = _load_data(pattern)
        reach = float(roi_reach or 3.0)
        df_view, _ = (
            _roi_apply(df_f, pattern, reach, _on(roi_entered), _on(roi_trim))
            if _on(roi_entered) or _on(roi_trim) else (df_f, None)
        )
        metric_stats = _visible_segment_stats(native_stats, df_view)
        figure = build_trial_metrics_figure(
            metric_stats, group_by=group_by, pool_mode=pool_mode,
            distribution_mode=distribution_mode,
            show_violin_points=_on(distribution_show_points),
            stats_unit=stats_unit,
            spatial_unit_scale=spatial_unit_scale,
            spatial_unit_label=spatial_unit_label)
        elapsed = time.perf_counter() - started
        state = {
            "completed": time.time(),
            "epoch": int((polar_state or {}).get("epoch", 0)),
            "data": (polar_state or {}).get("data"),
            "seconds": round(elapsed, 4),
        }
        LOGGER.info("metrics.stage_done rows=%d seconds=%.3f", len(df_view), elapsed)
        return figure, state, (
            f"Trial metrics ready in {elapsed:.2f}s; checking optional Gandiva…"
        )


    @app.callback(
        Output("flow-figure-store", "data", allow_duplicate=True),
        Output("gandiva-render-state", "data"),
        Output("plot-status", "children", allow_duplicate=True),
        Input("metrics-render-state", "data"),
        Input("spatial-render-state", "data"),
        Input("gandiva-enabled", "value"),
        Input("polar-moving", "value"),
        Input("polar-walk", "value"),
        Input("polar-angle-source", "value"),
        Input("heatmap-metric", "value"),
        Input("heatmap-scale", "value"),
        Input("heatmap-cmin", "value"),
        Input("heatmap-cmax", "value"),
        Input("heatmap-crange", "value"),
        State("flow-max-radius", "value"),
        State("store-glob", "data"),
        State("vel-threshold", "value"),
        State("min-disp", "value"),
        State("trim-samples", "value"),
        State("jump-buffer", "value"),
        State("filter-configs", "value"),
        State("filter-vrs", "value"),
        State("filter-flyids", "value"),
        State("filter-scenes", "value"),
        State("filter-folders", "value"),
        State("trial-min", "value"),
        State("trial-max", "value"),
        State("step-min", "value"),
        State("step-max", "value"),
        State("vel-range-effective", "data"),
        State("disp-range", "value"),
        State("walk-range", "value"),
        State("group-by", "value"),
        State("pool-mode", "value"),
        State("subplot-ncols", "value"),
        State("rebase-origin", "value"),
        State("heatmap-binsize", "value"),
        State("heatmap-bound", "value"),
        State("roi-reach", "value"),
        State("roi-entered", "value"),
        State("roi-trim", "value"),
        State("viewport-store", "data"),
        prevent_initial_call=True,
    )
    def update_gandiva_stage(
            metrics_state, spatial_state, enabled, polar_moving, polar_walk,
            polar_angle_source, hm_metric, hm_scale, hm_cmin, hm_cmax,
            hm_crange, flow_max_radius, pattern, vel_thresh, min_disp, trim,
            jump_buf, cfg, vrs, fids, scenes, folders, trial_min, trial_max,
            step_min, step_max, vel_selection, disp_selection, walk_selection,
            group_by,
            pool_mode, ncols, rebase, hm_binsize, hm_bound, roi_reach,
            roi_entered, roi_trim, viewport):
        """Stage 5: calculate Gandiva only when explicitly enabled."""
        if not pattern or not (metrics_state or {}).get("completed"):
            LOGGER.info("gandiva.stage_skip reason=not_ready")
            return no_update, no_update, no_update
        trigger = str(ctx.triggered_id or "metrics-render-state")
        if (trigger == "spatial-render-state"
                and int((metrics_state or {}).get("epoch", -1))
                != int((spatial_state or {}).get("epoch", -2))):
            return no_update, no_update, no_update
        trigger_detail = (
            str((spatial_state or {}).get("trigger"))
            if trigger == "spatial-render-state" else trigger
        )
        if not _on(enabled):
            state = {
                "completed": time.time(), "enabled": False,
                "epoch": int((metrics_state or {}).get("epoch", 0)),
                "data": (metrics_state or {}).get("data"),
                "trigger": trigger, "trigger_detail": trigger_detail,
            }
            LOGGER.info("gandiva.stage_off epoch=%s", state["epoch"])
            return (
                _msg_figure(
                    "Gandiva is off. Enable it to calculate local vectors."
                ).to_plotly_json(),
                state,
                "Gandiva off; checking optional targets…",
            )
        started = time.perf_counter()
        df_f, _, _ = _filtered_df(
            pattern, vel_thresh, min_disp, trim, jump_buf,
            cfg, vrs, fids, scenes, folders, trial_min, trial_max,
            step_min, step_max, vel_selection, disp_selection, walk_selection)
        if df_f is None or len(df_f) == 0:
            message = "Gandiva skipped — no matching rows."
            return _msg_figure(message).to_plotly_json(), {
                "completed": time.time(), "enabled": True, "error": True,
            }, message
        reach = float(roi_reach or 3.0)
        df_view, _ = (
            _roi_apply(df_f, pattern, reach, _on(roi_entered), _on(roi_trim))
            if _on(roi_entered) or _on(roi_trim) else (df_f, None)
        )
        do_rebase = _on(rebase)
        df_spatial = rebase_to_origin(df_view) if do_rebase else df_view
        ncols_value = _resolve_panel_columns(
            ncols, df_spatial, group_by, pool_mode)
        bound_pct = float(hm_bound) if hm_bound not in (None, "") else 98.0
        figure = build_direction_field_figure(
            df_spatial, group_by, pool_mode, ncols=ncols_value,
            bin_size=hm_binsize, bound_pct=bound_pct,
            metric=hm_metric or "time", log_scale=(hm_scale == "log"),
            cmin=hm_cmin, cmax=hm_cmax, crange_mode=hm_crange,
            angle_source=polar_angle_source,
            moving_only=_on(polar_moving), walk_thresh=polar_walk,
            rois=None, reach_radius=reach, show_rois=False,
            max_radius=flow_max_radius)
        _apply_viewport_to_current_range(figure, viewport, max_span_mult=1.5)
        elapsed = time.perf_counter() - started
        state = {
            "completed": time.time(), "enabled": True,
            "epoch": int((metrics_state or {}).get("epoch", 0)),
            "data": (metrics_state or {}).get("data"),
            "seconds": round(elapsed, 4),
            "trigger": trigger, "trigger_detail": trigger_detail,
        }
        return figure.to_plotly_json(), state, (
            f"Gandiva ready in {elapsed:.2f}s; checking optional targets…"
        )


    @app.callback(
        Output("trajectory-plot", "figure", allow_duplicate=True),
        Output("heatmap-figure-store", "data", allow_duplicate=True),
        Output("flow-figure-store", "data", allow_duplicate=True),
        Output("roi-plot", "figure", allow_duplicate=True),
        Output("targets-render-state", "data"),
        Output("plot-status", "children", allow_duplicate=True),
        Input("gandiva-render-state", "data"),
        Input("roi-show", "value"),
        Input("roi-reach", "value"),
        State("store-glob", "data"),
        State("vel-threshold", "value"),
        State("min-disp", "value"),
        State("trim-samples", "value"),
        State("jump-buffer", "value"),
        State("filter-configs", "value"),
        State("filter-vrs", "value"),
        State("filter-flyids", "value"),
        State("filter-scenes", "value"),
        State("filter-folders", "value"),
        State("trial-min", "value"),
        State("trial-max", "value"),
        State("step-min", "value"),
        State("step-max", "value"),
        State("vel-range-effective", "data"),
        State("disp-range", "value"),
        State("walk-range", "value"),
        State("roi-entered", "value"),
        State("roi-trim", "value"),
        State("group-by", "value"),
        State("pool-mode", "value"),
        State("subplot-ncols", "value"),
        State("rebase-origin", "value"),
        State("color-by", "value"),
        State("heatmap-metric", "value"),
        State("trajectory-plot", "figure"),
        State("heatmap-figure-store", "data"),
        State("flow-figure-store", "data"),
        prevent_initial_call=True,
    )
    def update_targets_stage(
            gandiva_state, enabled, roi_reach, pattern, vel_thresh, min_disp,
            trim, jump_buf, cfg, vrs, fids, scenes, folders, trial_min,
            trial_max, step_min, step_max, vel_selection, disp_selection,
            walk_selection,
            roi_entered, roi_trim, group_by, pool_mode, ncols, rebase,
            color_by, hm_metric, current_trajectory, current_heat,
            current_flow):
        """Stage 6: defer all ROI accounting until after optional Gandiva."""
        trigger = str(ctx.triggered_id or "gandiva-render-state")
        # The normal load chain still reaches this callback from Gandiva.  A
        # direct target interaction is already an explicit request, however,
        # and must not be lost if the preceding stage Store is still settling
        # in the browser.
        if (not pattern or (
                trigger == "gandiva-render-state"
                and not (gandiva_state or {}).get("completed"))):
            LOGGER.info("targets.stage_skip reason=not_ready")
            return (no_update,) * 6
        gandiva_trigger = str((gandiva_state or {}).get("trigger") or "")
        transition_relevant = (
            trigger in {"roi-show", "roi-reach"}
            or gandiva_trigger == "metrics-render-state"
            or (gandiva_trigger == "spatial-render-state")
        )
        if not _on(enabled):
            state = {
                "completed": time.time(), "enabled": False,
                "epoch": int((gandiva_state or {}).get("epoch", 0)),
                "data": (gandiva_state or {}).get("data"),
                "trigger": trigger, "transition_relevant": transition_relevant,
            }
            LOGGER.info("targets.stage_off epoch=%s", state["epoch"])
            return (
                no_update, no_update, no_update,
                _msg_figure(
                    "Targets are off. Enable them to calculate ROI diagnostics."
                ),
                state, "Targets off; checking optional transitions…",
            )
        if (trigger == "gandiva-render-state"
                and gandiva_trigger != "metrics-render-state"):
            state = {
                "completed": time.time(), "enabled": True,
                "epoch": int((gandiva_state or {}).get("epoch", 0)),
                "data": (gandiva_state or {}).get("data"),
                "trigger": trigger,
                "transition_relevant": transition_relevant,
            }
            return (
                no_update, no_update, no_update, no_update,
                state, "Targets retained; checking transitions…",
            )
        started = time.perf_counter()
        LOGGER.info(
            "targets.stage_start trigger=%s epoch=%s",
            trigger, int((gandiva_state or {}).get("epoch", 0)),
        )
        df_f, _, _ = _filtered_df(
            pattern, vel_thresh, min_disp, trim, jump_buf,
            cfg, vrs, fids, scenes, folders, trial_min, trial_max,
            step_min, step_max, vel_selection, disp_selection, walk_selection)
        if df_f is None or len(df_f) == 0:
            message = "Target calculation skipped — no matching rows."
            return (
                no_update, no_update, no_update, _msg_figure(message),
                {"completed": time.time(), "enabled": True, "error": True},
                message,
            )
        _, _, metas = _load_data(pattern)
        rois = rois_by_config(metas)
        reach = float(roi_reach or 3.0)
        df_view, table = _roi_apply(
            df_f, pattern, reach, _on(roi_entered), _on(roi_trim))
        ncols_value = _resolve_panel_columns(
            ncols, df_view, group_by, pool_mode)
        group_items = list(_group_frames(
            df_view, group_by, pool_mode, ncols_value).items())
        do_rebase = _on(rebase)
        outcomes = (
            roi_outcome_by_segment(df_view, rois, reach) if rois else None
        )

        trajectory_out = no_update
        if current_trajectory and rois and not do_rebase:
            trajectory_out = go.Figure(current_trajectory)
            base_annotations = [
                annotation.to_plotly_json()
                for annotation in (trajectory_out.layout.annotations or [])
                if not str(getattr(annotation, "name", "") or "").startswith(
                    "td-target-overlay")
            ]
            base_shapes = [
                shape.to_plotly_json()
                for shape in (trajectory_out.layout.shapes or [])
                if not str(getattr(shape, "name", "") or "").startswith(
                    "td-target-overlay")
            ]
            trajectory_out.update_layout(
                shapes=base_shapes + _roi_overlay_shapes(
                    group_items, rois, reach),
                annotations=base_annotations + _roi_count_annotations(
                    group_items, table, outcomes),
            )

        spatial_groups = group_items
        roi_stats = _heatmap_roi_stats(spatial_groups, rois, reach) if rois else []
        roi_texts = _heatmap_roi_corner_texts(
            roi_stats, hm_metric or "time", _median_dt(df_view))

        def _target_spatial_overlay(current):
            if not current or not rois or do_rebase:
                return no_update
            figure = go.Figure(current)
            base_annotations = [
                annotation.to_plotly_json()
                for annotation in (figure.layout.annotations or [])
                if not str(getattr(annotation, "name", "") or "").startswith(
                    "td-target-overlay")
            ]
            base_shapes = [
                shape.to_plotly_json()
                for shape in (figure.layout.shapes or [])
                if not str(getattr(shape, "name", "") or "").startswith(
                    "td-target-overlay")
            ]
            figure.update_layout(
                shapes=base_shapes + _heatmap_roi_shapes(roi_stats, reach),
                annotations=base_annotations + _heatmap_roi_annotations(
                    roi_stats, roi_texts),
            )
            return figure.to_plotly_json()

        heat_out = _target_spatial_overlay(current_heat)
        flow_out = (
            _target_spatial_overlay(current_flow)
            if (gandiva_state or {}).get("enabled") else no_update
        )
        if not rois:
            figure = _msg_figure(
                "No target ROIs were found for the current configs.")
        elif table is None:
            figure = _msg_figure("No target trials are available.")
        else:
            figure = build_roi_swarm_figure(df_view, rois, reach, table=table)
        elapsed = time.perf_counter() - started
        state = {
            "completed": time.time(), "enabled": True,
            "epoch": int((gandiva_state or {}).get("epoch", 0)),
            "data": (gandiva_state or {}).get("data"),
            "seconds": round(elapsed, 4),
            "trigger": trigger, "transition_relevant": transition_relevant,
        }
        LOGGER.info(
            "targets.stage_done trigger=%s rows=%d panels=%d seconds=%.3f",
            trigger, len(df_view), len(group_items), elapsed,
        )
        return (
            trajectory_out, heat_out, flow_out, figure, state,
            f"Targets ready in {elapsed:.2f}s; checking optional transitions…",
        )
    
    
    @app.callback(
        Output("polar-plot", "figure", allow_duplicate=True),
        Output("custom-region-diagnostics-plot", "figure", allow_duplicate=True),
        Output("trial-metrics-plot", "figure", allow_duplicate=True),
        Output("custom-region-stats-store", "data", allow_duplicate=True),
        Output("polar-render-state", "data", allow_duplicate=True),
        Output("plot-status", "children", allow_duplicate=True),
        Input("custom-region-analysis-request", "data"),
        Input("distribution-mode", "value"),
        Input("distribution-show-points", "value"),
        Input("stats-unit", "value"),
        Input("spatial-unit-scale", "value"),
        Input("spatial-unit-label", "value"),
        State("view-render-state", "data"),
        State("store-glob", "data"),
        State("vel-threshold", "value"),
        State("min-disp", "value"),
        State("trim-samples", "value"),
        State("jump-buffer", "value"),
        State("filter-configs", "value"),
        State("filter-vrs", "value"),
        State("filter-flyids", "value"),
        State("filter-scenes", "value"),
        State("filter-folders", "value"),
        State("trial-min", "value"),
        State("trial-max", "value"),
        State("step-min", "value"),
        State("step-max", "value"),
        State("vel-range-effective", "data"),
        State("disp-range", "value"),
        State("walk-range", "value"),
        State("group-by", "value"),
        State("pool-mode", "value"),
        State("color-by", "value"),
        State("subplot-ncols", "value"),
        State("plot-points", "value"),
        State("render-mode", "value"),
        State("rebase-origin", "value"),
        State("roi-show", "value"),
        State("roi-reach", "value"),
        State("roi-entered", "value"),
        State("roi-trim", "value"),
        State("traj-trial-fraction", "value"),
        State("btn-traj-resample", "n_clicks"),
        State("polar-moving", "value"),
        State("polar-walk", "value"),
        State("polar-angle-source", "value"),
        State("polar-r-range", "value"),
        State("polar-min-point-frac", "value"),
        State("polar-min-animal-frac", "value"),
        State("custom-region-enabled", "value"),
        State("custom-regions-store", "data"),
        prevent_initial_call=True,
    )
    def update_custom_region_analysis(
            analysis_request, distribution_mode, distribution_show_points, stats_unit,
            spatial_unit_scale, spatial_unit_label,
            render_state, pattern, vel_thresh, min_disp, trim,
            jump_buf, cfg, vrs, fids, scenes, folders, trial_min, trial_max,
            step_min, step_max, vel_selection, disp_selection, walk_selection,
            group_by, pool_mode,
            color_by, ncols, max_points, render_mode, rebase, roi_show, roi_reach,
            roi_entered, roi_trim, traj_fraction, traj_sample_seed, polar_moving,
            polar_walk, polar_angle_source, polar_r_range, polar_min_point_frac,
            polar_min_animal_frac, enabled, regions):
        trigger = ctx.triggered_id
        figure_only = trigger in {
            "distribution-mode", "distribution-show-points",
            "spatial-unit-scale", "spatial-unit-label",
        }
        payload_unchanged = trigger in {
            "distribution-mode", "distribution-show-points", "stats-unit",
            "spatial-unit-scale", "spatial-unit-label",
        }
        if ((not analysis_request and not payload_unchanged)
                or not pattern or not render_state):
            return (no_update,) * 6
        started = time.perf_counter()
        df_f, _, _ = _filtered_df(
            pattern, vel_thresh, min_disp, trim, jump_buf,
            cfg, vrs, fids, scenes, folders, trial_min, trial_max,
            step_min, step_max, vel_selection, disp_selection, walk_selection)
        if df_f is None or len(df_f) == 0:
            message = _msg_figure("No rows match the active filters.")
            return (
                message, message, message, {}, no_update,
                "Observation windows have no matching rows.",
            )
    
        _, native_stats, metas = _load_data(pattern)
        rois = rois_by_config(metas)
        reach = float(roi_reach or 3.0)
        df_view, _ = (
            _roi_apply(df_f, pattern, reach, _on(roi_entered), _on(roi_trim))
            if _on(roi_entered) or _on(roi_trim) else (df_f, None)
        )
        ncols_value = _resolve_panel_columns(ncols, df_view, group_by, pool_mode)
        do_rebase = _on(rebase)
        position_frame = rebase_to_origin(df_view) if do_rebase else df_view
        region_on = _on(enabled)
        payload = (
            _custom_region_stats(
                df_view, regions, group_by, pool_mode, ncols_value,
                position_frame=position_frame,
            )
            if region_on else {"enabled": False, "regions": [], "panels": []}
        )
        diagnostic = build_custom_region_diagnostics_figure(
            payload, distribution_mode=distribution_mode,
            show_violin_points=_on(distribution_show_points),
            stats_unit=stats_unit,
            spatial_unit_scale=spatial_unit_scale,
            spatial_unit_label=spatial_unit_label,
        )
        store_payload = (
            no_update if payload_unchanged else _custom_region_store_payload(payload)
        )
        metric_stats = (
            _custom_region_segment_stats(
                df_view, regions, position_frame=position_frame)
            if region_on else _visible_segment_stats(native_stats, df_view)
        )
        metrics_fig = build_trial_metrics_figure(
            metric_stats, group_by=group_by, pool_mode=pool_mode,
            distribution_mode=distribution_mode,
            show_violin_points=_on(distribution_show_points),
            stats_unit=stats_unit,
            spatial_unit_scale=spatial_unit_scale,
            spatial_unit_label=spatial_unit_label,
        )
    
        polar_fig = no_update
        if not figure_only:
            sampled = df_view
            sampled_positions = rebase_to_origin(sampled) if do_rebase else sampled
            polar_source = (
                _custom_region_subset(sampled, regions, sampled_positions)
                if region_on else sampled
            )
            roi_outcomes = (
                roi_outcome_by_segment(df_view, rois, reach)
                if color_by == "roi" and rois else None
            )
            polar_fig = build_polar_figure(
                polar_source, group_by, pool_mode, ncols=ncols_value,
                color_by=color_by or "categorical",
                moving_only=_on(polar_moving), walk_thresh=polar_walk,
                max_points=_budget(
                    BUDGET_POLAR, BUDGET_POLAR_SPEED,
                    _render_mode(render_mode), max_points),
                rois=rois, reach_radius=reach,
                show_rois=bool(rois) and not do_rebase,
                roi_outcomes=roi_outcomes, r_range=polar_r_range,
                min_point_frac=polar_min_point_frac,
                min_animal_trial_frac=polar_min_animal_frac,
                angle_source=polar_angle_source,
                stats_unit=stats_unit,
            )
        elapsed = time.perf_counter() - started
        state = {
            "completed": time.time(),
            "operation": (
                "figure format" if figure_only
                else "observation windows"
            ),
            "epoch": int((render_state or {}).get("epoch", 0)),
            "timings": {"observation windows": round(elapsed, 4)},
        }
        return (
            polar_fig, diagnostic, metrics_fig, store_payload, state,
            (
                f"Distribution diagnostics updated in {elapsed:.2f}s."
                if figure_only else
                "Observation windows updated polar, grouped diagnostics and trial "
                f"metrics in {elapsed:.2f}s."
            ),
        )
    
    
    app.clientside_callback(
        """
        function(renderState, polarState) {
          if (!renderState || !renderState.completed) {
            return [true, window.dash_clientside.no_update,
                    window.dash_clientside.no_update];
          }
          return [false, 0, {
            pending:true,
            epoch:Number(renderState.epoch || 0),
            requested:Date.now()/1000,
            polarCompleted:Number((polarState || {}).completed || 0)
          }];
        }
        """,
        Output("stats-delay-interval", "disabled"),
        Output("stats-delay-interval", "n_intervals"),
        Output("stats-overlay-store", "data", allow_duplicate=True),
        Input("view-render-state", "data"),
        Input("polar-render-state", "data"),
        prevent_initial_call=True,
    )
    
    
    @app.callback(
        Output("stats-overlay-store", "data"),
        Input("stats-delay-interval", "n_intervals"),
        State("view-render-state", "data"),
        State("store-glob", "data"),
        State("vel-threshold", "value"),
        State("min-disp", "value"),
        State("trim-samples", "value"),
        State("jump-buffer", "value"),
        State("filter-configs", "value"),
        State("filter-vrs", "value"),
        State("filter-flyids", "value"),
        State("filter-scenes", "value"),
        State("filter-folders", "value"),
        State("trial-min", "value"),
        State("trial-max", "value"),
        State("step-min", "value"),
        State("step-max", "value"),
        State("vel-range-effective", "data"),
        State("disp-range", "value"),
        State("walk-range", "value"),
        State("group-by", "value"),
        State("pool-mode", "value"),
        State("roi-reach", "value"),
        State("roi-entered", "value"),
        State("roi-trim", "value"),
        State("traj-trial-fraction", "value"),
        State("btn-traj-resample", "n_clicks"),
        State("polar-moving", "value"),
        State("polar-walk", "value"),
        State("polar-angle-source", "value"),
        State("polar-r-range", "value"),
        State("polar-min-point-frac", "value"),
        State("polar-min-animal-frac", "value"),
        State("custom-region-enabled", "value"),
        State("custom-regions-store", "data"),
        State("stats-unit", "value"),
        State("rebase-origin", "value"),
        prevent_initial_call=True,
    )
    def compute_delayed_statistics(
            n_intervals, render_state, pattern, vel_thresh, min_disp, trim,
            jump_buf, cfg, vrs, fids, scenes, folders, trial_min, trial_max,
            step_min, step_max, vel_selection, disp_selection, walk_selection,
            group_by,
            pool_mode, roi_reach, roi_entered, roi_trim, traj_fraction,
            traj_sample_seed, polar_moving, polar_walk, polar_angle_source,
            polar_r_range, polar_min_point_frac, polar_min_animal_frac,
            custom_region_enabled, custom_regions, stats_unit, rebase):
        if not n_intervals or not pattern or not render_state:
            return no_update
        started = time.perf_counter()
        try:
            df_f, _, _ = _filtered_df(
                pattern, vel_thresh, min_disp, trim, jump_buf,
                cfg, vrs, fids, scenes, folders, trial_min, trial_max,
                step_min, step_max, vel_selection, disp_selection,
                walk_selection)
            raw_df, native_stats, _ = _load_data(pattern)
            if df_f is None or len(df_f) == 0:
                return {
                    "pending": False,
                    "error": "No rows available for statistical tests.",
                }
            reach = float(roi_reach or 3.0)
            df_view, _ = (
                _roi_apply(
                    df_f, pattern, reach, _on(roi_entered), _on(roi_trim))
                if _on(roi_entered) or _on(roi_trim) else (df_f, None)
            )
            polar_frame = df_view
            region_payload = None
            if _on(custom_region_enabled):
                metric_positions = (
                    rebase_to_origin(df_view) if _on(rebase) else df_view
                )
                region_payload = _custom_region_stats(
                    df_view, custom_regions, group_by, pool_mode, 2,
                    position_frame=metric_positions,
                )
                metric_stats = _custom_region_segment_stats(
                    df_view, custom_regions, metric_positions)
                polar_positions = (
                    rebase_to_origin(polar_frame) if _on(rebase) else polar_frame
                )
                polar_frame = _custom_region_subset(
                    polar_frame, custom_regions, polar_positions)
            else:
                metric_stats = _visible_segment_stats(native_stats, df_view)
            payload = _statistics_payload(
                metric_stats, polar_frame, raw_df, group_by, pool_mode,
                polar_moving, polar_walk, polar_angle_source, polar_r_range,
                polar_min_point_frac, polar_min_animal_frac,
                stats_unit=stats_unit,
                custom_region_payload=region_payload)
            payload["seconds"] = round(time.perf_counter() - started, 4)
            payload["epoch"] = int(render_state.get("epoch", 0))
            return payload
        except Exception as exc:
            LOGGER.exception("stats.failed")
            return {"pending": False, "error": f"{type(exc).__name__}: {exc}"}
    
    
    app.clientside_callback(
        """
        function(payload) {
          payload = payload || {};
          if (payload.pending) {
            return ['calculating…', 'calculating…', 'calculating…'];
          }
          if (payload.error) {
            return ['stats unavailable', 'stats unavailable', 'stats unavailable'];
          }
          if (!payload.completed) {
            return ['stats queued', 'stats queued', 'start-angle stats queued'];
          }
          function apply(graphId, additions) {
            var container=document.getElementById(graphId);
            var gd=container&&container.querySelector('.js-plotly-plot');
            if (!gd||!window.Plotly||!gd.layout) return;
            var base=(gd.layout.annotations||[]).filter(function(a){
              return !a || String(a.name||'').indexOf('td-stats:')!==0;
            });
            window.Plotly.relayout(gd,{annotations:base.concat(additions)});
          }
          function axisRef(prefix,index) {
            return prefix+(index===0?'':String(index+1));
          }
          function statAnnotation(mark,metricIndex,x,hover) {
            var raw=String(mark.group||'');
            return {
              name:'td-stats:'+raw,
              xref:axisRef('x',metricIndex),
              yref:axisRef('y',metricIndex)+' domain',
              x:Number(x),y:0.985,xanchor:'center',yanchor:'top',
              showarrow:false,text:'<b>'+String(mark.letters||'')+'</b>',
              hovertext:hover||mark.hover||'',hoverlabel:{namelength:-1},
              bgcolor:'rgba(255,255,255,0.74)',borderpad:1,
              font:{size:11,color:'#6b4f00'}
            };
          }
          function appendSubtitle(graphId,marks,prefix) {
            var container=document.getElementById(graphId);
            var gd=container&&container.querySelector('.js-plotly-plot');
            if (!gd||!window.Plotly||!gd.layout) return;
            var subtitleMarker='<br><sup><b>';
            var byGroup={};
            (marks||[]).forEach(function(mark){
              byGroup[String(mark.group||'')]=mark;
            });
            gd.__tdStatsTitleBase=gd.__tdStatsTitleBase||{};
            var annotations=JSON.parse(JSON.stringify(gd.layout.annotations||[]));
            annotations.forEach(function(ann){
              var group=String((ann&&ann.hovertext)||'');
              var mark=byGroup[group];
              if(!mark)return;
              if(!gd.__tdStatsTitleBase[group] ||
                 String(ann.text||'').indexOf(subtitleMarker)<0){
                gd.__tdStatsTitleBase[group]=String(ann.text||'');
              }
              var base=gd.__tdStatsTitleBase[group];
              var text=String(mark.rayleigh_stars||'n/a');
              if(prefix==='polar' && mark.letters){
                text+=' · '+String(mark.letters);
              }
              ann.text=base+subtitleMarker+text+'</b></sup>';
              ann.hovertext=group;
              ann.hoverlabel={namelength:-1};
              ann.captureevents=true;
              ann.yshift=Math.max(Number(ann.yshift||0),18);
            });
            window.Plotly.relayout(gd,{annotations:annotations});
          }
          window.setTimeout(function(){
            var metricAdds=[];
            (payload.metric_marks||[]).forEach(function(marks,index){
              (marks||[]).forEach(function(mark){
                metricAdds.push(statAnnotation(
                  mark,index,mark.group_index,mark.hover
                ));
              });
            });
            apply('trial-metrics-plot',metricAdds);
            var regionAdds=[];
            (payload.region_marks||[]).forEach(function(marks,index){
              var count=Math.max(1,(marks||[]).reduce(function(best,mark){
                return Math.max(best,Number(mark.region_index||0)+1);
              },0));
              var spacing=0.72/count;
              (marks||[]).forEach(function(mark){
                var centre=Number(mark.group_index||0)+
                  (Number(mark.region_index||0)-(count-1)/2)*spacing;
                var hover=String(mark.region_name||'Window')+
                  ' · n='+Number(mark.n||0).toLocaleString()+
                  ' · '+Number(mark.significant_pairs||0)+
                  ' significant adjusted pair(s)';
                regionAdds.push(statAnnotation(mark,index,centre,hover));
              });
            });
            apply('custom-region-diagnostics-plot',regionAdds);
            appendSubtitle('polar-plot',payload.polar_marks||[],'polar');
            appendSubtitle(
              'initial-heading-plot',payload.start_marks||[],'initial'
            );
            if(window.dash_clientside.panel_order &&
               window.dash_clientside.panel_order.reapply){
              window.setTimeout(function(){
                window.dash_clientside.panel_order.reapply();
              },20);
            }
          },30);
          var elapsed=Number(payload.seconds||0).toFixed(2)+' s';
          return [
            'circular significance labels ready · '+elapsed,
            'non-parametric compact-letter labels ready · '+elapsed,
            'initial-angle significance labels ready · '+elapsed
          ];
        }
        """,
        Output("polar-stats-status", "children"),
        Output("metrics-stats-status", "children"),
        Output("initial-stats-status", "children"),
        Input("stats-overlay-store", "data"),
    )
    
    
    # Direction controls are intentionally isolated from the atomic dashboard
    # render above. Moving/source changes update the flow field and polar figure;
    # Rayleigh quality changes update only polar; heatmap metric/scale/range changes
    # update only the flow field. All paths reuse the filtered frame and do not
    # rebuild trajectories, occupancy bins, ROI diagnostics, trial metrics, or raw
    # traces.
    app.clientside_callback(
        "function(a,b,c,d,e,f,g,h,i,j,k,pattern){"
        "if(!pattern)return window.dash_clientside.no_update;"
        "return 'Updating direction views…';}",
        Output("plot-status", "children", allow_duplicate=True),
        Input("polar-moving", "value"),
        Input("polar-walk", "value"),
        Input("polar-angle-source", "value"),
        Input("polar-r-range", "value"),
        Input("polar-min-point-frac", "value"),
        Input("polar-min-animal-frac", "value"),
        Input("heatmap-metric", "value"),
        Input("heatmap-scale", "value"),
        Input("heatmap-cmin", "value"),
        Input("heatmap-cmax", "value"),
        Input("heatmap-crange", "value"),
        State("store-glob", "data"),
        prevent_initial_call=True,
    )
    
    
    @app.callback(
        Output("polar-plot", "figure", allow_duplicate=True),
        Output("flow-figure-store", "data", allow_duplicate=True),
        Output("polar-r-hist", "figure"),
        Output("polar-point-frac-hist", "figure"),
        Output("polar-animal-frac-hist", "figure"),
        Output("polar-render-state", "data"),
        Output("plot-status", "children", allow_duplicate=True),
        Output("data-summary", "children", allow_duplicate=True),
        Input("spatial-render-state", "data"),
        Input("color-by", "value"),
        Input("polar-moving", "value"),
        Input("polar-walk", "value"),
        Input("polar-angle-source", "value"),
        Input("polar-r-range", "value"),
        Input("polar-min-point-frac", "value"),
        Input("polar-min-animal-frac", "value"),
        State("heatmap-metric", "value"),
        State("heatmap-scale", "value"),
        State("heatmap-cmin", "value"),
        State("heatmap-cmax", "value"),
        State("heatmap-crange", "value"),
        State("store-glob", "data"),
        State("vel-threshold", "value"),
        State("min-disp", "value"),
        State("trim-samples", "value"),
        State("jump-buffer", "value"),
        State("filter-configs", "value"),
        State("filter-vrs", "value"),
        State("filter-flyids", "value"),
        State("filter-scenes", "value"),
        State("filter-folders", "value"),
        State("trial-min", "value"),
        State("trial-max", "value"),
        State("step-min", "value"),
        State("step-max", "value"),
        State("vel-range-effective", "data"),
        State("disp-range", "value"),
        State("walk-range", "value"),
        State("group-by", "value"),
        State("pool-mode", "value"),
        State("subplot-ncols", "value"),
        State("plot-points", "value"),
        State("render-mode", "value"),
        State("rebase-origin", "value"),
        State("heatmap-binsize", "value"),
        State("heatmap-bound", "value"),
        State("flow-max-radius", "value"),
        State("viewport-store", "data"),
        State("roi-show", "value"),
        State("roi-reach", "value"),
        State("roi-entered", "value"),
        State("roi-trim", "value"),
        State("traj-trial-fraction", "value"),
        State("btn-traj-resample", "n_clicks"),
        State("custom-region-enabled", "value"),
        State("custom-regions-store", "data"),
        State("stats-unit", "value"),
        State("data-summary", "children"),
        State("polar-render-state", "data"),
        prevent_initial_call=True,
    )
    def update_polar_only(render_state, color_by, polar_moving, polar_walk,
                          polar_angle_source,
                          polar_r_range, polar_min_point_frac,
                          polar_min_animal_frac, hm_metric, hm_scale, hm_cmin,
                          hm_cmax, hm_crange, pattern, vel_thresh, min_disp,
                          trim, jump_buf, cfg, vrs, fids, scenes, folders,
                          trial_min, trial_max, step_min, step_max, vel_selection,
                          disp_selection, walk_selection, group_by, pool_mode, ncols,
                          max_points, render_mode, rebase, hm_binsize, hm_bound,
                          flow_max_radius, viewport, roi_show, roi_reach,
                          roi_entered, roi_trim, traj_fraction, traj_sample_seed,
                          custom_region_enabled, custom_regions, stats_unit,
                          current_summary, current_polar_state):
        empty_hists = build_polar_quality_histograms(None, polar_r_range,
                                                      polar_min_point_frac,
                                                      polar_min_animal_frac)
        if not pattern or not render_state:
            return no_update, no_update, *empty_hists, no_update, no_update, no_update
    
        trigger = ctx.triggered_id
        initial_stage = (
            (render_state or {}).get("trigger") == "view-render-state"
            and int((current_polar_state or {}).get("epoch", -1))
            != int((render_state or {}).get("epoch", -2))
        )
        if (trigger == "spatial-render-state"
                and not initial_stage):
            # Grid-only edits do not invalidate polar statistics. Gandiva and
            # transitions subscribe to the spatial stage independently.
            return (no_update,) * 8
        polar_triggers = {
            "color-by", "polar-moving", "polar-walk", "polar-angle-source",
            "polar-r-range", "polar-min-point-frac", "polar-min-animal-frac",
        }
        effective_trigger = "spatial-render-state" if initial_stage else trigger
        refresh_polar = effective_trigger in polar_triggers or initial_stage
        # Gandiva owns a later opt-in callback; polar never holds it up.
        refresh_flow = False
        refresh_direction = refresh_polar or refresh_flow
        refresh_hists = initial_stage or refresh_polar
        if not refresh_polar and not refresh_hists:
            return (no_update,) * 8
        op_id = (
            _progress_begin(
                "direction",
                ["Filter/cache", "Direction aggregation", "Direction figures"],
                "Updating direction views from cached trajectory rows…",
            )
            if refresh_direction else None
        )
        started = time.perf_counter()
        stage_started = started
        timings = {}
        LOGGER.info(
            "direction.start trigger=%s moving=%s walk=%s angle=%s source=%r",
            trigger, _on(polar_moving), polar_walk, polar_angle_source, pattern,
        )
        df_f, _, _ = _filtered_df(
            pattern, vel_thresh, min_disp, trim, jump_buf,
            cfg, vrs, fids, scenes, folders, trial_min, trial_max,
            step_min, step_max, vel_selection, disp_selection, walk_selection)
        timings["filter/cache"] = time.perf_counter() - stage_started
        if op_id is not None:
            _progress_stage(
                op_id, 1, done=0, total=1,
                message="Aggregating per-segment circular vectors…",
            )
        if df_f is None or len(df_f) == 0:
            msg = _msg_figure("No trajectories match the active filters.")
            if op_id is not None:
                _progress_finish(
                    op_id,
                    "Direction update skipped — no rows match the active filters.",
                    failed=True,
                )
            histogram_out = empty_hists if refresh_hists else (no_update,) * 3
            return (
                msg if refresh_polar else no_update,
                msg.to_plotly_json() if refresh_flow else no_update,
                *histogram_out,
                no_update,
                ("Direction update skipped — no rows match the active filters."
                 if refresh_direction else no_update),
                no_update,
            )
    
        stage_started = time.perf_counter()
        _, _, metas = _load_data(pattern)
        rois = rois_by_config(metas)
        reach = float(roi_reach) if roi_reach else 3.0
        df_view, _ = (
            _roi_apply(df_f, pattern, reach, _on(roi_entered), _on(roi_trim))
            if _on(roi_entered) or _on(roi_trim) else (df_f, None)
        )
        df_polar = df_view
        if _on(custom_region_enabled):
            polar_positions = (
                rebase_to_origin(df_polar) if _on(rebase) else df_polar
            )
            df_polar = _custom_region_subset(
                df_polar, custom_regions, polar_positions)
        ray = None
        if refresh_hists:
            ray_metric = (
                color_by if color_by in ("velocity", "tortuosity") else "none")
            ray = rayleigh_by_segment(
                df_polar, _on(polar_moving), polar_walk, ray_metric,
                angle_source=polar_angle_source)
        timings["ray aggregation"] = time.perf_counter() - stage_started
        if op_id is not None:
            _progress_stage(
                op_id, 2, done=0, total=1,
                message="Drawing polar layers and quality histograms…",
            )
    
        stage_started = time.perf_counter()
        hists = (
            build_polar_quality_histograms(
                ray, polar_r_range, polar_min_point_frac, polar_min_animal_frac)
            if refresh_hists else (no_update,) * 3
        )
        polar_fig = no_update
        flow_fig = no_update
        quality = (
            _filter_polar_ray_table(
                ray, polar_r_range, polar_min_point_frac,
                polar_min_animal_frac)[1]
            if refresh_hists else {}
        )
        if refresh_direction:
            ncols_val = _resolve_panel_columns(
                ncols, df_view, group_by, pool_mode)
            mode = _render_mode(render_mode)
            want_rois = False
            if refresh_polar:
                roi_outcomes = (
                    roi_outcome_by_segment(df_polar, rois, reach)
                    if (color_by == "roi" or want_rois) and rois else None)
                polar_fig, quality = build_polar_figure(
                    df_polar, group_by, pool_mode, ncols=ncols_val,
                    color_by=color_by or "categorical",
                    moving_only=_on(polar_moving), walk_thresh=polar_walk,
                    max_points=_budget(
                        BUDGET_POLAR, BUDGET_POLAR_SPEED, mode, max_points),
                    rois=rois, reach_radius=reach,
                    show_rois=want_rois and not _on(rebase),
                    roi_outcomes=roi_outcomes,
                    r_range=polar_r_range, min_point_frac=polar_min_point_frac,
                    min_animal_trial_frac=polar_min_animal_frac,
                    return_summary=True, angle_source=polar_angle_source,
                    stats_unit=stats_unit)
            if refresh_flow:
                df_flow = rebase_to_origin(df_view) if _on(rebase) else df_view
                bound_pct = (
                    float(hm_bound) if hm_bound not in (None, "") else 98.0)
                flow_fig = build_direction_field_figure(
                    df_flow, group_by, pool_mode, ncols=ncols_val,
                    bin_size=hm_binsize, bound_pct=bound_pct,
                    metric=hm_metric or "time", log_scale=(hm_scale == "log"),
                    cmin=hm_cmin, cmax=hm_cmax, crange_mode=hm_crange,
                    angle_source=polar_angle_source,
                    moving_only=_on(polar_moving), walk_thresh=polar_walk,
                    rois=rois, reach_radius=reach,
                    show_rois=want_rois and not _on(rebase),
                    max_radius=flow_max_radius)
                _apply_viewport_to_current_range(
                    flow_fig, viewport, max_span_mult=1.5)
        timings["figure/histograms"] = time.perf_counter() - stage_started
        elapsed = time.perf_counter() - started
        timings["total"] = elapsed
    
        if not refresh_direction:
            LOGGER.info("polar.histograms trials=%d seconds=%.3f",
                        int(quality.get("after_animal", 0)), elapsed)
            return no_update, no_update, *hists, no_update, no_update, no_update
    
        state = {
            "completed": time.time(), "operation": "direction controls",
            "timings": {k: round(float(v), 4) for k, v in timings.items()},
            "trigger": str(effective_trigger),
            "stage_trigger": (render_state or {}).get("trigger"),
            "epoch": int((render_state or {}).get("epoch", 0)),
            "data": (render_state or {}).get("data"),
        }
        kept = int(quality.get("after_animal", 0))
        LOGGER.info("direction.done trigger=%s trials=%d seconds=%.3f",
                    trigger, kept, elapsed)
        ready_message = (
            f"Polar ready with {kept:,} trials in {elapsed:.2f}s."
            if refresh_polar
            else f"Ready — flow field updated in {elapsed:.2f}s."
        )
        if op_id is not None:
            _progress_finish(op_id, ready_message)
        summary_out = (
            re.sub(r"polar [\d,]+ trials", f"polar {kept:,} trials",
                   current_summary)
            if refresh_polar and isinstance(current_summary, str)
            else no_update
        )
        flow_data = (
            flow_fig.to_plotly_json() if flow_fig is not no_update else no_update)
        return (polar_fig, flow_data, *hists, state,
                ready_message, summary_out)


    @app.callback(
        Output("heading-time-plot", "figure"),
        Output("heading-time-status", "children", allow_duplicate=True),
        Output("heading-time-render-state", "data"),
        Input("polar-render-state", "data"),
        Input("heading-time-enabled", "value"),
        Input("heading-time-mode", "value"),
        Input("heading-time-representation", "value"),
        Input("heading-time-window", "value"),
        Input("heading-time-variability", "value"),
        Input("heading-time-angle-bin", "value"),
        State("store-glob", "data"),
        State("vel-threshold", "value"),
        State("min-disp", "value"),
        State("trim-samples", "value"),
        State("jump-buffer", "value"),
        State("filter-configs", "value"),
        State("filter-vrs", "value"),
        State("filter-flyids", "value"),
        State("filter-scenes", "value"),
        State("filter-folders", "value"),
        State("trial-min", "value"),
        State("trial-max", "value"),
        State("step-min", "value"),
        State("step-max", "value"),
        State("vel-range-effective", "data"),
        State("disp-range", "value"),
        State("walk-range", "value"),
        State("color-by", "value"),
        State("group-by", "value"),
        State("pool-mode", "value"),
        State("subplot-ncols", "value"),
        State("plot-points", "value"),
        State("render-mode", "value"),
        State("roi-reach", "value"),
        State("roi-entered", "value"),
        State("roi-trim", "value"),
        State("polar-moving", "value"),
        State("polar-walk", "value"),
        State("polar-angle-source", "value"),
        State("polar-r-range", "value"),
        State("polar-min-point-frac", "value"),
        State("polar-min-animal-frac", "value"),
        State("custom-region-enabled", "value"),
        State("custom-regions-store", "data"),
        State("rebase-origin", "value"),
        prevent_initial_call=True,
    )
    def update_heading_time(
            polar_state, enabled, heading_mode, representation, window_seconds,
            heading_variability, angle_bin_degrees,
            pattern, vel_thresh, min_disp,
            trim, jump_buf, cfg, vrs, fids, scenes, folders, trial_min,
            trial_max, step_min, step_max, vel_selection, disp_selection,
            walk_selection, color_by,
            group_by, pool_mode, ncols, max_points, render_mode, roi_reach,
            roi_entered, roi_trim, polar_moving, polar_walk,
            polar_angle_source, polar_r_range, polar_min_point_frac,
            polar_min_animal_frac, custom_region_enabled, custom_regions,
            rebase):
        """Focused optional panel; the mounted prior figure stays until return."""
        trigger = str(ctx.triggered_id or "polar-render-state")
        if not _on(enabled):
            return no_update, "off", {
                "completed": time.time(), "enabled": False,
                "epoch": int((polar_state or {}).get("epoch", 0)),
            }
        if not pattern or not (polar_state or {}).get("completed"):
            return (no_update,) * 3
        started = time.perf_counter()
        LOGGER.info(
            "heading_time.start trigger=%s mode=%s epoch=%s",
            trigger, heading_mode, int((polar_state or {}).get("epoch", 0)),
        )
        df_f, _, _ = _filtered_df(
            pattern, vel_thresh, min_disp, trim, jump_buf,
            cfg, vrs, fids, scenes, folders, trial_min, trial_max,
            step_min, step_max, vel_selection, disp_selection, walk_selection)
        if df_f is None or len(df_f) == 0:
            message = "Heading time skipped — no rows match the filters."
            return _msg_figure(message), message, {
                "completed": time.time(), "enabled": True, "error": True,
            }
        reach = float(roi_reach or 3.0)
        df_view, _ = (
            _roi_apply(df_f, pattern, reach, _on(roi_entered), _on(roi_trim))
            if _on(roi_entered) or _on(roi_trim) else (df_f, None)
        )
        if _on(custom_region_enabled):
            positions = rebase_to_origin(df_view) if _on(rebase) else df_view
            df_view = _custom_region_subset(
                df_view, custom_regions, positions)
        ncols_value = _resolve_panel_columns(
            ncols, df_view, group_by, pool_mode)
        mode = _render_mode(render_mode)
        _, _, metas = _load_data(pattern)
        rois = rois_by_config(metas)
        roi_outcomes = (
            roi_outcome_by_segment(df_view, rois, reach)
            if color_by == "roi" and rois else None
        )
        figure = build_heading_time_figure(
            df_view, group_by, pool_mode, ncols=ncols_value,
            mode=heading_mode, angle_source=polar_angle_source,
            moving_only=_on(polar_moving), walk_thresh=polar_walk,
            r_range=polar_r_range,
            min_point_frac=polar_min_point_frac,
            min_animal_trial_frac=polar_min_animal_frac,
            max_points=_budget(
                BUDGET_HEADING_TIME, BUDGET_HEADING_TIME_SPEED,
                mode, max_points),
            window_seconds=window_seconds,
            show_variability=_on(heading_variability),
            color_by=color_by or "categorical",
            roi_outcomes=roi_outcomes,
            representation=representation,
            angle_bin_degrees=angle_bin_degrees,
        )
        elapsed = time.perf_counter() - started
        meta = dict(figure.layout.meta or {})
        requested_window = meta.get("requested_window", "auto")
        requested_key = (
            str(requested_window)
            if requested_window in ("auto", "full")
            else f"{float(requested_window):g}"
        )
        time_bin = float(meta.get("time_bin_seconds") or 0)
        window_label = (
            f"auto {time_bin:g} s windows"
            if meta.get("window_mode") == "auto"
            else (f"full resolution ({time_bin:g} s)"
                  if meta.get("window_mode") == "full"
                  else f"{time_bin:g} s windows")
        )
        state = {
            "completed": time.time(), "enabled": True,
            "epoch": int((polar_state or {}).get("epoch", 0)),
            "data": (polar_state or {}).get("data"),
            "mode": "animal" if heading_mode == "animal" else "trial",
            "representation": (
                "density" if representation == "density" else "traces"),
            "requested_window": requested_key,
            "window_label": window_label,
            "variability": _on(heading_variability),
            "angle_bin_degrees": float(angle_bin_degrees or 5),
            "seconds": round(elapsed, 4), "trigger": trigger,
        }
        LOGGER.info(
            "heading_time.done trigger=%s mode=%s rows=%d seconds=%.3f",
            trigger, state["mode"], len(df_view), elapsed,
        )
        label = (
            "density layers" if state["representation"] == "density"
            else ("animal mean" if state["mode"] == "animal" else "trial traces")
        )
        return figure, (
            f"{label} ready · {window_label} · {elapsed:.2f} s"
        ), state


    app.clientside_callback(
        "function(figure){setTimeout(function(){"
        "if(window.dash_clientside.panel_order&&"
        "window.dash_clientside.panel_order.reapply){"
        "window.dash_clientside.panel_order.reapply();}"
        "if(window.dash_clientside.clean_layout&&"
        "window.dash_clientside.clean_layout.refresh){"
        "window.dash_clientside.clean_layout.refresh();}"
        "},40);return '';}",
        Output("anim-dummy", "children", allow_duplicate=True),
        Input("heading-time-plot", "figure"),
        prevent_initial_call=True,
    )


    # Attach a debounced Plotly relayout listener directly to the visible
    # trajectory graph. This avoids feeding every drag/wheel event through Dash's
    # `relayoutData` callback machinery while the gesture is in progress.
    app.clientside_callback(
        "function(fig){setTimeout(function(){"
        "var g=document.querySelector('#trajectory-plot .js-plotly-plot');"
        "if(g&&window.__attachViewportSync){window.__attachViewportSync(g,'traj');}"
        "},120);return '';}",
        Output("anim-dummy", "children", allow_duplicate=True),
        Input("trajectory-plot", "figure"),
        prevent_initial_call=True,
    )
    
    # The heatmap uses a 1:1 aspect lock (scaleanchor). Dash's Plotly.react update
    # path crashes on that with "axis scaling" when the figure is applied to a graph
    # that isn't at full size yet, and never recovers — so the heatmap stays blank.
    # A fresh Plotly.newPlot re-initialises cleanly and renders.
    #
    # Fingerprint the structural content so metric/scale restyles never trigger a
    # second newPlot. Section navigation is intentionally absent from this callback.
    app.clientside_callback(
        "function(hfig,metric,scale,entered,trim,colorRange,rangeMode,colorData,variants){setTimeout(function(){"
        "var hc=document.getElementById('heatmap-plot');"
        "var hg=hc&&hc.querySelector('.js-plotly-plot');"
        "var fp='';try{var L=(hfig&&hfig.layout)||{};"
        # Fingerprint tracks BINNING/structure only (trace count, z/x dimensions,
        # height, axis ranges) — NOT zmin/zmax, which are colouring that the client
        # restyles in place. Including them made a metric/scale swap look like a
        # structural change (dcc.Graph syncs restyled zmin back to the prop) and
        # triggered a needless newPlot flash on the *next* swap.
        "fp=JSON.stringify((hfig&&hfig.data||[]).map(function(t){"
        "return [t.type,(t.z&&t.z.length)||0,(t.x&&t.x.length)||0];}))"
        "+'|'+JSON.stringify((L.shapes||[]).map(function(s){return [s.x0,s.x1,s.y0,s.y1,s.xref,s.yref];}))"
        "+'|'+((L.annotations||[]).length)"
        "+'|'+(L.height||0)+'|'+JSON.stringify(L.xaxis&&L.xaxis.range)"
        "+'|'+JSON.stringify(L.yaxis&&L.yaxis.range);}catch(e){}"
        "if(hg&&window.Plotly&&hfig&&hfig.data&&hfig.data.length){"
        "var changed=hg.__hmfp!==fp;"
        "var needPaint=changed||!hg.__hmPainted;"
        "if(needPaint){"
        "window.__hmSuppress=true;"
        "try{hc.style.transition='none';hc.style.opacity='1';}catch(e){}"
        "try{window.Plotly.newPlot(hg,hfig.data,hfig.layout,{scrollZoom:true,displayModeBar:true,displaylogo:false,"
        "toImageButtonOptions:{format:'png',scale:3},edits:{shapePosition:true}});"
        "hg.__hmfp=fp;hg.__hmPainted=true;"
        "if(window.__attachHeatSync){window.__attachHeatSync(hg,true);}}catch(e){}"
        "if(window.dash_clientside.clean_layout){window.dash_clientside.clean_layout.refresh();}"
        "try{hc.style.opacity='1';}catch(e){}"
        "setTimeout(function(){window.__hmSuppress=false;},250);"
        "}else{try{"
        "var incoming=((hfig.layout||{}).annotations||[]);"
        "var extras=((hg.layout||{}).annotations||[]).filter(function(a){"
        "return a&&['custom-region-label','td-clean-scale','td-stats'].indexOf(a.name)>=0;});"
        "window.Plotly.relayout(hg,{annotations:incoming.concat(extras)});"
        "(hfig.data||[]).forEach(function(trace,index){"
        "if(trace&&trace.colorscale){window.Plotly.restyle(hg,{colorscale:[trace.colorscale]},[index]);}"
        "});"
        "}catch(e){}"
        "}"
        # Swap in the current metric/scale variant IN PLACE (Plotly.restyle) — instant,
        # no re-init, no flash. Every metric×scale was precomputed at bin time, so
        # flipping the metric/scale radios only touches z/zmin/zmax/colorbar here.
        "try{var e=(entered&&entered.indexOf('on')>=0)?1:0;"
        "var t=(trim&&trim.indexOf('on')>=0)?1:0;"
        "var key='e'+e+'_t'+t+'_'+(metric||'time')+'_'+(scale||'lin');"
        "var v=variants&&variants[key];"
        "if(v&&window.__restyleHeatmap){"
        "window.__restyleHeatmap(hg,v,metric,scale,colorRange,rangeMode,colorData);"
        "}}catch(e){}"
        "}"
        "},90);return '';}",
        Output("anim-dummy", "children", allow_duplicate=True),
        Input("heatmap-figure-store", "data"),
        Input("heatmap-metric", "value"),
        Input("heatmap-scale", "value"),
        Input("roi-entered", "value"),
        Input("roi-trim", "value"),
        Input("heatmap-color-range", "value"),
        Input("heatmap-crange", "value"),
        Input("heatmap-color-values", "data"),
        State("heatmap-variants", "data"),
        prevent_initial_call=True,
    )
    
    app.clientside_callback(
        "function(ffig){setTimeout(function(){"
        "var fc=document.getElementById('flow-plot');"
        "var fg=fc&&fc.querySelector('.js-plotly-plot');"
        "if(fg&&window.Plotly&&ffig&&ffig.data&&ffig.data.length){"
        "try{window.Plotly.newPlot(fg,ffig.data,ffig.layout,"
        "{scrollZoom:true,displayModeBar:true,displaylogo:false,"
        "toImageButtonOptions:{format:'png',scale:3},edits:{shapePosition:true}});"
        "if(window.__attachViewportSync){window.__attachViewportSync(fg,'flow',true);}"
        "if(window.dash_clientside.clean_layout){window.dash_clientside.clean_layout.refresh();}"
        "}catch(e){}}"
        "},90);return '';}",
        Output("anim-dummy", "children", allow_duplicate=True),
        Input("flow-figure-store", "data"),
        prevent_initial_call=True,
    )
    
    app.clientside_callback(
        """
        function(radius) {
          var value = Math.max(0.01, Math.min(0.98, Number(radius || 0.49)));
          var container = document.getElementById('flow-plot');
          var gd = container && container.querySelector('.js-plotly-plot');
          if (!gd || !window.Plotly || !gd.data || !gd.layout) {
            return window.dash_clientside.no_update;
          }
          var meta = gd.layout.meta || {};
          var previous = Math.max(0.01, Number(meta.max_radius || 0.49));
          var ratio = value / previous;
          if (!Number.isFinite(ratio) || Math.abs(ratio - 1) < 1e-9) {
            return window.dash_clientside.no_update;
          }
          gd.data.forEach(function(trace, index) {
            if (!trace || !trace.meta || !trace.meta.gandiva_arrow) return;
            var xs = Array.from(trace.x || []);
            var ys = Array.from(trace.y || []);
            for (var i = 0; i + 1 < xs.length; i += 3) {
              var ox = Number(xs[i]), oz = Number(ys[i]);
              var tx = Number(xs[i+1]), tz = Number(ys[i+1]);
              if (![ox,oz,tx,tz].every(Number.isFinite)) continue;
              xs[i+1] = ox + ratio * (tx - ox);
              ys[i+1] = oz + ratio * (tz - oz);
            }
            window.Plotly.restyle(gd, {x:[xs], y:[ys]}, [index]);
          });
          meta.max_radius = value;
          window.Plotly.relayout(gd, {meta:meta});
          return 'Gandiva arrows rescaled locally · radius ' + value.toFixed(2);
        }
        """,
        Output("plot-status", "children", allow_duplicate=True),
        Input("flow-max-radius", "value"),
        prevent_initial_call=True,
    )
    
    
    app.clientside_callback(
        """
        function(diff) {
          if (window.dash_clientside.style_patch) {
            return window.dash_clientside.style_patch.render(diff);
          }
          return 'Loading mounted style patcher…';
        }
        """,
        Output("anim-dummy", "children", allow_duplicate=True),
        Input("visual-style-diff-store", "data"),
        prevent_initial_call=True,
    )
    
    
    # Pre-fill LUT editor with current configs → their auto-humanised names
    @app.callback(
        Output("lut-editor", "value"),
        Input("btn-prefill-lut", "n_clicks"),
        State("store-glob", "data"),
        prevent_initial_call=True,
    )
    def prefill_lut(n, pattern):
        if not pattern:
            return no_update
        df, _, _ = _load_data(pattern)
        if df is None:
            return no_update
        configs = sorted(df["ConfigFile"].unique())
        mapping = {c: humanise_config(c) for c in configs}
        return json.dumps(mapping, indent=2)
    
    
    # Apply legacy LUT overrides as a mounted text patch. Config labels never alter
    # row selection, bins, statistics, or trace geometry.
    @app.callback(
        Output("lut-status", "children"),
        Output("visual-style-diff-store", "data", allow_duplicate=True),
        Input("btn-apply-lut", "n_clicks"),
        State("lut-editor", "value"),
        prevent_initial_call=True,
    )
    def apply_lut(n, lut_text):
        try:
            parsed = json.loads(lut_text or "{}")
            if not isinstance(parsed, dict):
                raise ValueError("must be a JSON object")
            keys = set(_USER_LUT) | {str(key) for key in parsed}
            before = {key: humanise_config(key) for key in keys}
            _USER_LUT.clear()
            _USER_LUT.update({str(k): str(v) for k, v in parsed.items()})
            renames = [
                {
                    "kind": "config", "raw": key,
                    "old": before[key], "new": humanise_config(key),
                }
                for key in sorted(keys)
                if before[key] != humanise_config(key)
            ]
            diff = {
                "changed_paths": [
                    f"group_labels.config.{item['raw']}.name"
                    for item in renames
                ],
                "renames": renames,
                "requires_replot": False,
                "applied": time.time(),
            }
            return (
                f"Applied {len(_USER_LUT)} name(s) directly to mounted plots",
                diff,
            )
        except Exception as e:
            return f"Error: {e}", no_update
    
    
    @app.callback(
        Output("visual-style-editor", "value"),
        Input("btn-prefill-visual-style", "n_clicks"),
        Input("data-generation", "data"),
        State("store-glob", "data"),
        prevent_initial_call=True,
    )
    def prefill_visual_style(_n, _generation, pattern):
        df = None
        if pattern:
            df, _, _ = _load_data(pattern)
        return json.dumps(_visual_style_payload(df), indent=2)
    
    
    @app.callback(
        Output("visual-style-status", "children"),
        Output("visual-style-store", "data"),
        Output("visual-style-diff-store", "data"),
        Output("btn-plot", "n_clicks", allow_duplicate=True),
        Input("btn-apply-visual-style", "n_clicks"),
        State("visual-style-editor", "value"),
        State("visual-style-store", "data"),
        State("btn-plot", "n_clicks"),
        prevent_initial_call=True,
    )
    def apply_visual_style(_n, style_text, previous_style, plot_clicks):
        """Apply JSON styles, patching browser-only diffs without a full rebuild."""
        try:
            parsed = json.loads(style_text or "{}")
            if not isinstance(parsed, dict):
                raise ValueError("must be a JSON object")
            merged = _deep_merge(_VISUAL_STYLE_DEFAULTS, parsed)
            widths = merged["gandiva"].get("arrow_widths", [])
            opacities = merged["gandiva"].get("arrow_opacities", [])
            breaks = merged["gandiva"].get("density_breaks", [])
            if len(widths) != 5 or len(opacities) != 5 or len(breaks) != 6:
                raise ValueError(
                    "gandiva requires 5 arrow_widths, 5 arrow_opacities and "
                    "6 density_breaks")
            config_entries = merged.get("group_labels", {}).get(
                "config", merged.get("categories", {}).get("config", {}))
            for raw, entry in config_entries.items():
                if isinstance(entry, dict) and entry.get("name"):
                    _USER_LUT[str(raw)] = str(entry["name"])
            previous = (
                previous_style
                if isinstance(previous_style, dict) else _VISUAL_STYLE)
            changed_paths = _style_diff_paths(previous, merged)
            renames = _style_rename_entries(previous, merged, changed_paths)
            browser_sections = {
                "loop_observer", "region_observer", "spatial_layout",
            }
            browser_only = all(
                (
                    len(path) == 4
                    and path[0] == "group_labels"
                    and path[3] == "name"
                )
                or path[:2] == ("heatmap", "colorscale")
                or (path and path[0] in browser_sections)
                for path in changed_paths
            )
            diff_payload = {
                "changed_paths": [".".join(path) for path in changed_paths],
                "renames": renames,
                "heatmap_colorscale": merged.get(
                    "heatmap", {}).get("colorscale"),
                "requires_replot": bool(changed_paths and not browser_only),
                "applied": time.time(),
            }
            _VISUAL_STYLE.clear()
            _VISUAL_STYLE.update(merged)
            if not changed_paths:
                message = "No style changes detected."
                next_clicks = no_update
            elif browser_only:
                message = (
                    f"Applied {len(changed_paths)} text/layout style "
                    "change(s) directly to mounted plots."
                )
                next_clicks = no_update
            else:
                message = (
                    f"Applied {len(changed_paths)} style change(s); rebuilding "
                    "only because rendered marks changed."
                )
                next_clicks = (plot_clicks or 0) + 1
            return (
                message,
                merged,
                diff_payload,
                next_clicks,
            )
        except Exception as exc:
            return f"Style error: {exc}", no_update, no_update, no_update
    
    
    def _export_loop_observer_html(
            enabled=False, x=0, z=0, radius=3, *, rings=None, active=None,
            match_mode="any", visual_style=None):
        """Standalone controls + browser-local loop renderer for offline exports."""
        try:
            cx = float(x)
        except (TypeError, ValueError):
            cx = 0.0
        try:
            cz = float(z)
        except (TypeError, ValueError):
            cz = 0.0
        try:
            radius_value = float(radius)
        except (TypeError, ValueError):
            radius_value = 3.0
        cx = cx if np.isfinite(cx) else 0.0
        cz = cz if np.isfinite(cz) else 0.0
        radius_value = (
            radius_value if np.isfinite(radius_value) and radius_value > 0 else 3.0)
        cleaned_rings = []
        for index, ring in enumerate(rings if isinstance(rings, list) else []):
            if not isinstance(ring, dict):
                continue
            try:
                ring_x = float(ring.get("x", 0))
                ring_z = float(ring.get("z", 0))
                ring_radius = float(ring.get("radius", 3))
            except (TypeError, ValueError):
                continue
            if not (np.isfinite(ring_x) and np.isfinite(ring_z)
                    and np.isfinite(ring_radius) and ring_radius > 0):
                continue
            cleaned_rings.append({
                "id": str(ring.get("id", f"ring-{index + 1}")),
                "name": str(ring.get("name", f"Ring {index + 1}")),
                "x": ring_x, "z": ring_z, "radius": ring_radius,
            })
        if not cleaned_rings:
            cleaned_rings = [{
                "id": "ring-1", "name": "Ring 1",
                "x": cx, "z": cz, "radius": radius_value,
            }]
        active_id = str(active or cleaned_rings[0]["id"])
        if active_id not in {ring["id"] for ring in cleaned_rings}:
            active_id = cleaned_rings[0]["id"]
        selected = next(ring for ring in cleaned_rings if ring["id"] == active_id)
        cx, cz, radius_value = (
            selected["x"], selected["z"], selected["radius"])
        settings = json.dumps({
            "enabled": bool(enabled),
            "rings": cleaned_rings,
            "active": active_id,
            "matchMode": match_mode if match_mode in ("any", "all") else "any",
            "style": visual_style or _VISUAL_STYLE_DEFAULTS,
        })
        module = (
            Path(__file__).with_name("assets") / "loop_observer.js"
        ).read_text(encoding="utf-8")
        checked = " checked" if enabled else ""
        controls = f"""
    <div class="loop-export">
      <div class="loop-export-controls">
        <label><input id="export-loop-enabled" type="checkbox"{checked}>
          Curtain-ring observer</label>
        <select id="export-loop-active"></select>
        <button id="export-loop-add" type="button">+</button>
        <button id="export-loop-delete" type="button">×</button>
        <select id="export-loop-match">
          <option value="any">Any ring</option>
          <option value="all">All rings</option>
        </select>
        <label>X <input id="export-loop-x" type="number" step="any" value="{cx:g}"></label>
        <label>Z <input id="export-loop-z" type="number" step="any" value="{cz:g}"></label>
        <label>Radius <input id="export-loop-radius" type="number" min="0.001"
          step="any" value="{radius_value:g}"></label>
        <span id="export-loop-status">Enable the observer to inspect crossings.</span>
      </div>
      <div class="loop-export-note">Drag the gold ring. Muted paths are before first
        entry; saturated paths show the future after entry.</div>
      <div id="export-loop-plot"></div>
    </div>
    <script>{module}</script>
    """
        boot = r"""
    <script>
    (function (settings) {
      "use strict";
      var source = document.getElementById("export-trajectory-plot");
      var plot = document.getElementById("export-loop-plot");
      var enabled = document.getElementById("export-loop-enabled");
      var activeSelect = document.getElementById("export-loop-active");
      var addButton = document.getElementById("export-loop-add");
      var deleteButton = document.getElementById("export-loop-delete");
      var matchSelect = document.getElementById("export-loop-match");
      var xInput = document.getElementById("export-loop-x");
      var zInput = document.getElementById("export-loop-z");
      var radiusInput = document.getElementById("export-loop-radius");
      var status = document.getElementById("export-loop-status");
    
      function value(input, fallback) {
        var numeric = Number(input.value);
        return Number.isFinite(numeric) ? numeric : fallback;
      }
      function activeRing() {
        return settings.rings.filter(function (ring) {
          return String(ring.id) === String(settings.active);
        })[0] || settings.rings[0];
      }
      function syncSelector() {
        activeSelect.innerHTML = "";
        settings.rings.forEach(function (ring) {
          var option = document.createElement("option");
          option.value = ring.id;
          option.textContent = ring.name;
          activeSelect.appendChild(option);
        });
        activeSelect.value = settings.active;
        matchSelect.value = settings.matchMode;
      }
      function loadActive() {
        var ring = activeRing();
        settings.active = ring.id;
        xInput.value = ring.x;
        zInput.value = ring.z;
        radiusInput.value = ring.radius;
        syncSelector();
      }
      function updateActive() {
        var ring = activeRing();
        ring.x = value(xInput, ring.x);
        ring.z = value(zInput, ring.z);
        ring.radius = Math.max(0.001, value(radiusInput, ring.radius));
      }
      function attach() {
        if (!plot || !plot.on) return;
        if (plot.__exportLoopRelayout && plot.removeListener) {
          plot.removeListener("plotly_relayout", plot.__exportLoopRelayout);
        }
        plot.__exportLoopRelayout = function (eventData) {
          var index = null;
          Object.keys(eventData || {}).some(function (key) {
            var match = /^shapes\[(\d+)\]\./.exec(key);
            if (!match) return false;
            index = Number(match[1]);
            return true;
          });
          var shape = index === null ? null :
            ((plot.layout && plot.layout.shapes) || [])[index];
          if (!shape) return;
          var nameMatch = /^loop-observer-ring:(.+)$/.exec(String(shape.name || ""));
          if (!nameMatch) return;
          var ring = settings.rings.filter(function (item) {
            return String(item.id) === nameMatch[1];
          })[0];
          if (!ring) return;
          var cx = (Number(shape.x0) + Number(shape.x1)) / 2;
          var cz = (Number(shape.y0) + Number(shape.y1)) / 2;
          var radius = (
            Math.abs(Number(shape.x1) - Number(shape.x0)) +
            Math.abs(Number(shape.y1) - Number(shape.y0))
          ) / 4;
          ring.x = Number(cx.toPrecision(10));
          ring.z = Number(cz.toPrecision(10));
          ring.radius = Number(Math.max(0.001, radius).toPrecision(10));
          settings.active = ring.id;
          loadActive();
          render();
        };
        plot.on("plotly_relayout", plot.__exportLoopRelayout);
      }
      function render() {
        if (!enabled.checked) {
          plot.style.display = "none";
          status.textContent = "Loop observer off.";
          return;
        }
        plot.style.display = "block";
        updateActive();
        var built = window.TrajectoryLoopObserver.build(
          {data: source.data || [], layout: source.layout || {}},
          settings.rings, settings.active, settings.matchMode, settings.style
        );
        status.textContent = built.status;
        var config = {
          scrollZoom: true,
          displayModeBar: true,
          displaylogo: false,
          edits: {shapePosition: true}
        };
        var promise = plot.__loopPainted ?
          Plotly.react(plot, built.data, built.layout, config) :
          Plotly.newPlot(plot, built.data, built.layout, config);
        Promise.resolve(promise).then(function () {
          plot.__loopPainted = true;
          attach();
        });
      }
      enabled.checked = Boolean(settings.enabled);
      syncSelector();
      loadActive();
      activeSelect.addEventListener("change", function () {
        settings.active = activeSelect.value;
        loadActive();
        render();
      });
      matchSelect.addEventListener("change", function () {
        settings.matchMode = matchSelect.value === "all" ? "all" : "any";
        render();
      });
      addButton.addEventListener("click", function () {
        var suffix = 1;
        var used = {};
        settings.rings.forEach(function (ring) { used[ring.id] = true; });
        while (used["ring-" + suffix]) suffix += 1;
        var base = activeRing();
        var ring = {
          id:"ring-" + suffix, name:"Ring " + suffix,
          x:base.x + base.radius * 0.55,
          z:base.z + base.radius * 0.55,
          radius:base.radius
        };
        settings.rings.push(ring);
        settings.active = ring.id;
        loadActive();
        render();
      });
      deleteButton.addEventListener("click", function () {
        if (settings.rings.length <= 1) return;
        settings.rings = settings.rings.filter(function (ring) {
          return ring.id !== settings.active;
        });
        settings.active = settings.rings[0].id;
        loadActive();
        render();
      });
      [enabled, xInput, zInput, radiusInput].forEach(function (control) {
        control.addEventListener("change", render);
      });
      render();
    }(__LOOP_SETTINGS__));
    </script>
    """.replace("__LOOP_SETTINGS__", settings)
        return controls + boot
    
    
    def _export_transition_observer_html(
            bundle, outcome="crossed", display_metric="fraction",
            count_min=None, count_max=None) -> str:
        """Embed the transition grid and browser-local clicked-bin drill-down."""
        if not isinstance(bundle, dict) or not bundle.get("figure"):
            return "<p>Transition probability was not enabled for this export.</p>"
        selected = outcome if outcome in TRANSITION_OUTCOMES else "crossed"
        selected_metric = (
            display_metric
            if display_metric in TRANSITION_METRICS else "fraction")
        figure = go.Figure(bundle["figure"])
        active = bundle.get("variants", {}).get(selected, {})
        display = active.get("displays", {}).get(selected_metric, {})
        try:
            count_min_value = float(count_min)
        except (TypeError, ValueError):
            count_min_value = None
        try:
            count_max_value = float(count_max)
        except (TypeError, ValueError):
            count_max_value = None
        if count_min_value is not None and not np.isfinite(count_min_value):
            count_min_value = None
        if count_max_value is not None and not np.isfinite(count_max_value):
            count_max_value = None
        active_zmin = display.get("zmin")
        active_zmax = display.get("zmax")
        if selected_metric == "count":
            if count_min_value is not None:
                active_zmin = max(0.0, count_min_value)
            if count_max_value is not None:
                active_zmax = max(0.0, count_max_value)
            if not float(active_zmax or 0) > float(active_zmin or 0):
                active_zmax = max(
                    float(display.get("zmax") or 1),
                    float(active_zmin or 0) + 1,
                )
        for index, trace in enumerate(figure.data):
            if str(getattr(trace, "type", "")).lower() != "heatmap":
                continue
            if index < len(display.get("z", [])):
                trace.z = display["z"][index]
                trace.customdata = active["customdata"][index]
                trace.zmin = active_zmin
                trace.zmax = active_zmax
                trace.colorbar = display.get("colorbar")
                trace.hovertemplate = display.get("hovertemplate")
        heat_html = figure.to_html(
            full_html=False, include_plotlyjs=False,
            config=dict(scrollZoom=True, displaylogo=False),
            div_id="export-transition-plot",
        )
        settings = {
            key: value for key, value in bundle.items()
            if key != "figure"
        }
        module = (
            Path(__file__).with_name("assets") / "transition_observer.js"
        ).read_text(encoding="utf-8")
        encoded = json.dumps(settings, separators=(",", ":"), ensure_ascii=False)
        count_min_text = "" if count_min_value is None else f"{count_min_value:g}"
        count_max_text = "" if count_max_value is None else f"{count_max_value:g}"
        return f"""
    <div class="transition-export">
      <div class="transition-export-controls">
        <label>Outcome
          <select id="export-transition-outcome">
            <option value="crossed">Crossed opposite half</option>
            <option value="ended">Ended opposite half</option>
          </select>
        </label>
        <label>Colour
          <select id="export-transition-metric">
            <option value="fraction">Fraction (%)</option>
            <option value="count">Successful trials (n)</option>
          </select>
        </label>
        <label>Count min
          <input id="export-transition-count-min" type="number" min="0" step="any"
            value="{count_min_text}" placeholder="auto">
        </label>
        <label>Count max
          <input id="export-transition-count-max" type="number" min="0" step="any"
            value="{count_max_text}" placeholder="auto">
        </label>
        <span id="export-transition-status">{bundle.get("message", "")}</span>
      </div>
      <div class="transition-export-note">Each cell conditions on unique trials
        that entered it. Click a coloured cell to overlay successful paths; click
        a blank cell to clear them.</div>
      {heat_html}
    </div>
    <script>{module}</script>
    <script>
    (function (bundle, initialOutcome, initialMetric, initialMin, initialMax) {{
      "use strict";
      var selector = document.getElementById("export-transition-outcome");
      var metricSelector = document.getElementById("export-transition-metric");
      var countMin = document.getElementById("export-transition-count-min");
      var countMax = document.getElementById("export-transition-count-max");
      selector.value = initialOutcome;
      metricSelector.value = initialMetric;
      var controller = window.TransitionProbabilityObserver.attachExport({{
        heatId: "export-transition-plot",
        sourceId: "export-trajectory-plot",
        statusId: "export-transition-status",
        bundle: bundle,
        outcome: initialOutcome,
        metric: initialMetric,
        countMin: initialMin,
        countMax: initialMax
      }});
      selector.addEventListener("change", function () {{
        controller.setOutcome(selector.value);
      }});
      metricSelector.addEventListener("change", function () {{
        controller.setMetric(metricSelector.value);
      }});
      function updateCountRange() {{
        controller.setCountRange(countMin.value, countMax.value);
      }}
      countMin.addEventListener("change", updateCountRange);
      countMax.addEventListener("change", updateCountRange);
    }})({encoded}, {json.dumps(selected)}, {json.dumps(selected_metric)},
        {json.dumps(count_min_value)}, {json.dumps(count_max_value)});
    </script>
    """
    
    
    def _compose_export_html(
            traj, heat, flow, roi, polar, metrics, vel, disp,
            initial_heading, raw, *, include_raw, summary, share_state,
                             heading_time=None,
                             loop_enabled=False, loop_x=0, loop_z=0, loop_radius=3,
                             loop_rings=None, loop_active=None,
                             loop_match_mode="any", visual_style=None,
                             transition_bundle=None,
                             transition_outcome="crossed",
                             transition_metric="fraction",
                             transition_count_min=None,
                             transition_count_max=None):
        """Build one offline-capable report with a single embedded plotly.js."""
        cfgd = dict(scrollZoom=True, displaylogo=False)
        traj_h = traj.to_html(
            full_html=False, include_plotlyjs=True, config=cfgd,
            div_id="export-trajectory-plot")
        loop_h = _export_loop_observer_html(
            loop_enabled, loop_x, loop_z, loop_radius,
            rings=loop_rings, active=loop_active, match_mode=loop_match_mode,
            visual_style=visual_style)
        transition_h = _export_transition_observer_html(
            transition_bundle, transition_outcome, transition_metric,
            transition_count_min, transition_count_max)
        heat_h = heat.to_html(full_html=False, include_plotlyjs=False, config=cfgd)
        flow_h = flow.to_html(full_html=False, include_plotlyjs=False, config=cfgd)
        roi_h = roi.to_html(full_html=False, include_plotlyjs=False, config=cfgd)
        polar_h = polar.to_html(full_html=False, include_plotlyjs=False, config=cfgd)
        heading_time = heading_time or _msg_figure(
            "Heading over time was disabled for this export.")
        heading_time_h = heading_time.to_html(
            full_html=False, include_plotlyjs=False, config=cfgd)
        metrics_h = metrics.to_html(full_html=False, include_plotlyjs=False, config=cfgd)
        vel_h = vel.to_html(full_html=False, include_plotlyjs=False)
        disp_h = disp.to_html(full_html=False, include_plotlyjs=False)
        initial_heading_h = initial_heading.to_html(
            full_html=False, include_plotlyjs=False, config=cfgd)
        raw_h = (raw.to_html(full_html=False, include_plotlyjs=False, config=cfgd)
                 if include_raw else "<p>No raw trace columns selected.</p>")
        return f"""<!DOCTYPE html>
    <html><head><meta charset="utf-8"><title>Daari Deepa Export</title>
    <style>body{{font-family:system-ui,sans-serif;margin:18px;color:#222}}
    h2{{margin:0 0 6px}} h3{{margin:18px 0 4px;font-size:14px;color:#555}}
    .info{{background:#e9ecef;padding:8px;border-radius:4px;font-size:13px;margin:6px 0}}
    .row{{display:flex;gap:10px}}.row>div{{flex:1;min-width:0}}
    .share{{font-size:11px;color:#888;word-break:break-all}}
    .flowlegend{{display:flex;align-items:center;justify-content:flex-end;gap:24px;
    padding:5px 14px;border:1px solid #edf1f7;border-radius:6px;background:#fbfcfe}}
    .loop-export{{border:1px solid #ead79a;border-radius:7px;background:#fffdf7;
    padding:8px;margin:8px 0 14px}}
    .loop-export-controls{{display:flex;align-items:center;gap:12px;flex-wrap:wrap;
    font-size:11px;color:#473a18}}
    .loop-export-controls input[type=number]{{width:72px}}
    .loop-export-controls span{{margin-left:auto;color:#6b5d32}}
    .loop-export-note{{font-size:10px;color:#75683f;margin:5px 0}}
    .transition-export{{border:1px solid rgba(94,74,130,.22);border-radius:7px;
    padding:8px;margin:8px 0 14px;background:linear-gradient(180deg,#f8f5fb,#fff 80px)}}
    .transition-export-controls{{display:flex;align-items:center;gap:12px;flex-wrap:wrap;
    font-size:11px;color:#493b67}}
    .transition-export-controls span{{margin-left:auto;color:#5e4a82}}
    .transition-export-note{{font-size:10px;color:#756b85;margin:5px 0}}
    .flowlegend>span{{font-size:9px;font-weight:650;color:#475467;text-transform:uppercase}}
    .flowwheel{{position:relative;width:64px;height:64px;font-size:8px;font-weight:700;color:#667085}}
    .flowwheel i{{position:absolute;inset:8px;border-radius:50%;
    background:radial-gradient(circle,#fff 0 40%,transparent 42%),
    conic-gradient(from 0deg,#ed5f5f,#eded5f,#5fed5f,#5feded,#5f5fed,#ed5fed,#ed5f5f)}}
    .flowwheel b{{position:absolute;z-index:1;font-weight:700}}
    .flowwheel .f{{top:0;left:50%;transform:translateX(-50%)}}
    .flowwheel .r{{right:0;top:50%;transform:translateY(-50%)}}
    .flowwheel .b{{bottom:0;left:50%;transform:translateX(-50%)}}
    .flowwheel .l{{left:0;top:50%;transform:translateY(-50%)}}
    .flowabundance{{display:flex;align-items:flex-end;gap:12px;font-size:8px;color:#667085}}
    .flowabundance em{{display:grid;justify-items:center;gap:5px;font-style:normal}}
    .flowabundance i{{display:block;position:relative;width:34px;background:#18212f}}
    .flowabundance .lo{{height:1px;opacity:.16}}.flowabundance .mid{{height:2px;opacity:.52}}
    .flowabundance .hi{{height:3px;opacity:.94}}
    .credit{{font-size:12px;margin-top:22px;color:#667085}}
    .credit a{{color:#2563eb;text-decoration:none;font-weight:650}}</style>
    </head><body>
    <h2>Daari Deepa</h2>
    <div class="info">{summary or ''}</div>
    <div class="share">State: <code>{share_state or ''}</code></div>
    <h3>Trajectories</h3>{traj_h}
    <h3>Loop observer</h3>{loop_h}
    <h3>Heatmap</h3>{heat_h}
    <h3>Transition probability</h3>{transition_h}
    <h3>Gandiva plot</h3>
    <div class="flowlegend"><span>Mean direction</span>
    <div class="flowwheel" aria-label="Circular direction colour legend">
    <b class="f">F</b><b class="r">R</b><b class="b">B</b><b class="l">L</b><i></i></div>
    <span>Abundance</span><div class="flowabundance">
    <em><i class="lo"></i>low</em><em><i class="mid"></i>medium</em>
    <em><i class="hi"></i>high</em></div></div>{flow_h}
    <h3>Polar</h3>{polar_h}
    <h3>Heading over time</h3>{heading_time_h}
    <h3>Target diagnostics</h3>{roi_h}
    <h3>Trial metrics</h3>{metrics_h}
    <h3>Diagnostics: raw starting-heading null distribution</h3>{initial_heading_h}
    <h3>Velocity / Displacement</h3><div class="row"><div>{vel_h}</div><div>{disp_h}</div></div>
    <h3>Raw traces</h3>{raw_h}
    <div class="credit"><a href="{REPO_URL}">❤️ by pvnkmrksk</a></div>
    </body></html>"""
    
    
    # Export — rebuild figures server-side so the HTML always embeds real data.
    @app.callback(
        Output("download-html", "data"),
        Output("plot-status", "children", allow_duplicate=True),
        Input("btn-export", "n_clicks"),
        State("store-glob", "data"),
        State("vel-threshold", "value"),
        State("min-disp", "value"),
        State("trim-samples", "value"),
        State("jump-buffer", "value"),
        State("group-by", "value"),
        State("pool-mode", "value"),
        State("color-by", "value"),
        State("animate-toggle", "value"),
        State("heatmap-binsize", "value"),
        State("heatmap-scale", "value"),
        State("heatmap-bound", "value"),
        State("heatmap-metric", "value"),
        State("heatmap-cmin", "value"),
        State("heatmap-cmax", "value"),
        State("heatmap-crange", "value"),
        State("transition-enabled", "value"),
        State("gandiva-enabled", "value"),
        State("transition-outcome", "value"),
        State("transition-metric", "value"),
        State("transition-count-min", "value"),
        State("transition-count-max", "value"),
        State("transition-split-z", "value"),
        State("transition-min-trials", "value"),
        State("filter-configs", "value"),
        State("filter-vrs", "value"),
        State("filter-flyids", "value"),
        State("filter-scenes", "value"),
        State("filter-folders", "value"),
        State("trial-min", "value"),
        State("trial-max", "value"),
        State("step-min", "value"),
        State("step-max", "value"),
        State("raw-columns", "value"),
        State("subplot-ncols", "value"),
        State("plot-points", "value"),
        State("traj-trial-fraction", "value"),
        State("btn-traj-resample", "n_clicks"),
        State("loop-enabled", "value"),
        State("loop-x", "value"),
        State("loop-z", "value"),
        State("loop-radius", "value"),
        State("loop-rings-store", "data"),
        State("loop-active-ring", "value"),
        State("loop-match-mode", "value"),
        State("render-mode", "value"),
        State("rebase-origin", "value"),
        State("roi-show", "value"),
        State("roi-reach", "value"),
        State("roi-entered", "value"),
        State("roi-trim", "value"),
        State("polar-r-range", "value"),
        State("polar-min-point-frac", "value"),
        State("polar-min-animal-frac", "value"),
        State("polar-moving", "value"),
        State("polar-walk", "value"),
        State("polar-angle-source", "value"),
        State("heading-time-enabled", "value"),
        State("heading-time-mode", "value"),
        State("heading-time-representation", "value"),
        State("heading-time-window", "value"),
        State("heading-time-variability", "value"),
        State("heading-time-angle-bin", "value"),
        State("vel-range-effective", "data"),
        State("disp-range", "value"),
        State("walk-range", "value"),
        State("flow-max-radius", "value"),
        State("stats-unit", "value"),
        State("distribution-mode", "value"),
        State("distribution-show-points", "value"),
        State("observation-paired-lines", "value"),
        State("spatial-unit-scale", "value"),
        State("spatial-unit-label", "value"),
        State("viewport-store", "data"),
        State("data-summary", "children"),
        State("url", "search"),
        prevent_initial_call=True,
    )
    def export_html(n, pattern, vel_thresh, min_disp, trim, jump_buf, group_by, pool_mode,
                    color_by, animate, hm_binsize, hm_scale, hm_bound, hm_metric,
                    hm_cmin, hm_cmax, hm_crange,
                    transition_enabled, gandiva_enabled, transition_outcome,
                    transition_metric,
                    transition_count_min, transition_count_max,
                    transition_split_z, transition_min_trials,
                    cfg, vrs, fids, scenes, folders,
                    trial_min, trial_max, step_min, step_max,
                    raw_cols, ncols, max_points, traj_fraction, traj_sample_seed,
                    loop_enabled, loop_x, loop_z, loop_radius,
                    loop_rings, loop_active, loop_match_mode, render_mode,
                    rebase, roi_show, roi_reach, roi_entered, roi_trim,
                    polar_r_range, polar_min_point_frac, polar_min_animal_frac,
                    polar_moving, polar_walk, polar_angle_source,
                    heading_time_enabled, heading_time_mode,
                    heading_time_representation, heading_time_window,
                    heading_time_variability, heading_time_angle_bin,
                    vel_selection, disp_selection, walk_selection,
                    flow_max_radius,
                    stats_unit, distribution_mode, distribution_show_points,
                    observation_paired_lines,
                    spatial_unit_scale, spatial_unit_label,
                    viewport, summary, url_search):
        if not pattern:
            LOGGER.warning("export.rejected reason=missing_source")
            return no_update, "Load data before exporting."
    
        started = time.perf_counter()
        op_id = _progress_begin(
            "export",
            ["Filter/cache", "Build figures", "Assemble offline HTML"],
            "Preparing the filtered export dataset…",
        )
        LOGGER.info("export.start mode=%s source=%r", _render_mode(render_mode), pattern)
    
        df_f, df_sub, _stats_sub = _filtered_df(
            pattern, vel_thresh, min_disp, trim, jump_buf,
            cfg, vrs, fids, scenes, folders, trial_min, trial_max,
            step_min, step_max, vel_selection, disp_selection, walk_selection,
            need_stats=True)
        if df_f is None or len(df_f) == 0:
            _progress_finish(
                op_id,
                "Export skipped — no rows match the active filters.",
                failed=True,
            )
            LOGGER.warning("export.rejected reason=no_filtered_rows source=%r", pattern)
            return no_update, "Export skipped — no rows match the active filters."
    
        _progress_stage(
            op_id, 1, done=0, total=1,
            message=f"Building export figures from {len(df_f):,} retained rows…",
        )
        do_animate = bool(animate) and "on" in (animate or [])
        do_rebase = bool(rebase) and "on" in (rebase or [])
        mode = _render_mode(render_mode)
        df_native, native_stats, metas = _load_data(pattern)
        rois = rois_by_config(metas)
        reach = float(roi_reach) if roi_reach else 3.0
        needs_roi = (
            _on(roi_show) or _on(roi_entered) or _on(roi_trim)
            or color_by == "roi"
        )
        df_view, table = (
            _roi_apply(df_f, pattern, reach, _on(roi_entered), _on(roi_trim))
            if needs_roi else (df_f, None)
        )
        ncols_val = _resolve_panel_columns(ncols, df_view, group_by, pool_mode)
        df_plot = rebase_to_origin(df_view) if do_rebase else df_view
        want_rois = _on(roi_show) and bool(rois)
        roi_counts = table if (want_rois and table is not None) else None
        roi_outcomes = (roi_outcome_by_segment(df_view, rois, reach)
                        if (color_by == "roi" or want_rois) and rois else None)
        traj_budget = _budget(BUDGET_SVG if do_animate else BUDGET_GL,
                              BUDGET_SVG_SPEED if do_animate else BUDGET_GL_SPEED,
                              mode, max_points)
        df_traj_sample = _sample_trajectory_segments(
            df_plot, traj_fraction, traj_sample_seed)
        df_traj_sample = mask_stationary_trajectory_points(
            df_traj_sample, _on(polar_moving), polar_walk)
        df_traj = (
            _decimate_frame(df_traj_sample, traj_budget)
            if mode == "speed" else df_traj_sample
        )
        df_heat = df_plot
        df_polar = df_view
        bound_pct = float(hm_bound) if hm_bound not in (None, "") else 98.0
        shared_fit = ((_robust_range(df_plot, bound_pct)
                       if bound_pct < 100 else _shared_range(df_plot))
                      if len(df_plot) else None)
        traj = build_trajectory_figure(df_traj, group_by, pool_mode, ncols=ncols_val,
                                       color_by=color_by or "categorical",
                                       animate=do_animate,
                                       max_points=len(df_traj) if mode == "speed" else max_points,
                                       rois=rois, reach_radius=reach,
                                       show_rois=want_rois and not do_rebase,
                                       roi_counts=roi_counts,
                                       roi_outcomes=roi_outcomes,
                                       view_range=shared_fit)
        heat = build_heatmap_figure(df_heat, group_by, pool_mode, ncols=ncols_val,
                                    bin_size=hm_binsize, log_scale=(hm_scale == "log"),
                                    bound_pct=bound_pct,
                                    metric=hm_metric or "time",
                                    cmin=hm_cmin, cmax=hm_cmax, crange_mode=hm_crange,
                                    rois=rois if want_rois and not do_rebase else None,
                                    reach_radius=reach)
        transition_bundle = (
            build_transition_probability_bundle(
                df_plot, group_by, pool_mode, ncols=ncols_val,
                bin_size=hm_binsize, bound_pct=bound_pct,
                split_z=transition_split_z,
                min_trials=transition_min_trials,
                outcome=transition_outcome,
                display_metric=transition_metric,
            )
            if _on(transition_enabled) else None
        )
        flow = (
            build_direction_field_figure(
                df_plot, group_by, pool_mode, ncols=ncols_val,
                bin_size=hm_binsize, bound_pct=bound_pct,
                metric=hm_metric or "time", log_scale=(hm_scale == "log"),
                cmin=hm_cmin, cmax=hm_cmax, crange_mode=hm_crange,
                angle_source=polar_angle_source,
                moving_only=_on(polar_moving), walk_thresh=polar_walk,
                rois=rois, reach_radius=reach,
                show_rois=want_rois and not do_rebase,
                max_radius=flow_max_radius)
            if _on(gandiva_enabled)
            else _msg_figure("Gandiva was disabled for this export.")
        )
        polar = build_polar_figure(
            df_polar, group_by, pool_mode, ncols=ncols_val,
            color_by=color_by or "categorical",
            moving_only=_on(polar_moving), walk_thresh=polar_walk,
            max_points=_budget(BUDGET_POLAR, BUDGET_POLAR_SPEED, mode, max_points),
            rois=rois, reach_radius=reach, show_rois=want_rois and not do_rebase,
            roi_outcomes=roi_outcomes, r_range=polar_r_range,
            min_point_frac=polar_min_point_frac,
            min_animal_trial_frac=polar_min_animal_frac,
            angle_source=polar_angle_source, stats_unit=stats_unit)
        heading_time = (
            build_heading_time_figure(
                df_polar, group_by, pool_mode, ncols=ncols_val,
                mode=heading_time_mode, angle_source=polar_angle_source,
                moving_only=_on(polar_moving), walk_thresh=polar_walk,
                r_range=polar_r_range,
                min_point_frac=polar_min_point_frac,
                min_animal_trial_frac=polar_min_animal_frac,
                max_points=_budget(
                    BUDGET_HEADING_TIME, BUDGET_HEADING_TIME_SPEED,
                    mode, max_points),
                window_seconds=heading_time_window,
                show_variability=_on(heading_time_variability),
                color_by=color_by or "categorical",
                roi_outcomes=roi_outcomes,
                representation=heading_time_representation,
                angle_bin_degrees=heading_time_angle_bin,
            )
            if _on(heading_time_enabled)
            else _msg_figure("Heading over time was disabled for this export.")
        )
        roi_fig = (build_roi_swarm_figure(df_view, rois, reach, table=table)
                   if want_rois and table is not None
                   else _msg_figure("No target diagnostics are available for this selection."))
        if not _on(observation_paired_lines):
            for trace in roi_fig.data:
                meta = getattr(trace, "meta", None)
                if isinstance(meta, dict) and meta.get("td_pairing"):
                    trace.visible = False
        metrics_fig = build_trial_metrics_figure(
            _visible_segment_stats(native_stats, df_view),
            group_by=group_by,
            pool_mode=pool_mode,
            distribution_mode=distribution_mode,
            show_violin_points=_on(distribution_show_points),
            stats_unit=stats_unit,
            spatial_unit_scale=spatial_unit_scale,
            spatial_unit_label=spatial_unit_label,
        )
        token = _DATA_TOKEN_BY_PATTERN.get(_pattern_key(pattern))
        native_velocity = _VELOCITY_CACHE.get(token)
        if native_velocity is None:
            native_velocity = smoothed_velocity(df_native, 10)
            if token is not None:
                _VELOCITY_CACHE[token] = native_velocity
        vel_fig = build_velocity_histogram(df_native, velocity_values=native_velocity)
        disp_fig = build_displacement_histogram(native_stats)
        initial_heading_fig = build_initial_heading_distribution(df_native)
        raw = build_raw_trace_figure(
            df_view, raw_cols or [],
            max_points=_budget(BUDGET_RAW, BUDGET_RAW_SPEED, mode, max_points))
    
        if viewport and not viewport.get("reset"):
            for f in (traj, heat):
                if viewport.get("xaxis"):
                    f.update_xaxes(range=viewport["xaxis"])
                if viewport.get("yaxis"):
                    f.update_yaxes(range=viewport["yaxis"])
            _apply_viewport_to_current_range(flow, viewport, max_span_mult=1.5)
            if transition_bundle:
                transition_figure = go.Figure(transition_bundle["figure"])
                _apply_viewport_to_current_range(
                    transition_figure, viewport, max_span_mult=1.5)
                transition_bundle = dict(transition_bundle)
                transition_bundle["figure"] = transition_figure.to_plotly_json()
    
        _progress_stage(
            op_id, 2, done=0, total=1,
            message="Embedding Plotly and all figures into one offline HTML file…",
        )
        content = _compose_export_html(
            traj, heat, flow, roi_fig, polar,
            metrics_fig, vel_fig, disp_fig,
            initial_heading_fig, raw,
            include_raw=bool(raw_cols), summary=summary,
            share_state=url_search, heading_time=heading_time,
            loop_enabled=_on(loop_enabled), loop_x=loop_x, loop_z=loop_z,
            loop_radius=loop_radius, loop_rings=loop_rings,
            loop_active=loop_active, loop_match_mode=loop_match_mode,
            visual_style=_VISUAL_STYLE,
            transition_bundle=transition_bundle,
            transition_outcome=transition_outcome,
            transition_metric=transition_metric,
            transition_count_min=transition_count_min,
            transition_count_max=transition_count_max,
        )
    
        ts = time.strftime("%Y%m%d_%H%M%S")
        filename = f"dari_deepa_export_{ts}.html"
        LOGGER.info(
            "export.done filename=%s bytes=%d rows=%d seconds=%.3f",
            filename, len(content.encode("utf-8")), len(df_view),
            time.perf_counter() - started,
        )
        _progress_finish(
            op_id,
            f"Ready — export built as {filename} "
            f"({len(content.encode('utf-8')) / 1_000_000:.1f} MB).",
        )
        return (dict(content=content, filename=filename),
                f"Export ready — {filename} ({len(content.encode('utf-8')) / 1_000_000:.1f} MB).")
    
    
    # ---------------------------------------------------------------------------
    # Clientside playback (sticky bar drives native Plotly frames, no round-trips)
    # ---------------------------------------------------------------------------
    
    _JS_GD = ("var c=document.getElementById('trajectory-plot');"
              "var gd=c&&c.querySelector('.js-plotly-plot');")
    
    app.clientside_callback(
        "function(n){" + _JS_GD +
        "if(gd&&window.Plotly){window.Plotly.animate(gd,null,{frame:{duration:120,redraw:true},"
        "fromcurrent:true,transition:{duration:0},mode:'immediate'});}"
        "return '';}",
        Output("anim-dummy", "children", allow_duplicate=True),
        Input("anim-play", "n_clicks"), prevent_initial_call=True,
    )
    
    app.clientside_callback(
        "function(n){" + _JS_GD +
        "if(gd&&window.Plotly){window.Plotly.animate(gd,[null],{mode:'immediate',"
        "frame:{duration:0,redraw:false},transition:{duration:0}});}"
        "return '';}",
        Output("anim-dummy", "children", allow_duplicate=True),
        Input("anim-pause", "n_clicks"), prevent_initial_call=True,
    )
    
    app.clientside_callback(
        "function(v){" + _JS_GD +
        "if(gd&&window.Plotly){var fr=(gd._transitionData&&gd._transitionData._frames)||[];"
        "var nf=fr.length; if(!nf) return '';"
        "var f=Math.round(v/100*(nf-1));"
        "window.Plotly.animate(gd,[String(f)],{mode:'immediate',frame:{duration:0,redraw:true},"
        "transition:{duration:0}});}"
        "return '';}",
        Output("anim-dummy", "children", allow_duplicate=True),
        Input("anim-slider", "value"), prevent_initial_call=True,
    )
    
    # Show the playback bar only when the trajectory figure actually has frames.
    app.clientside_callback(
        "function(fig){var has=fig&&fig.frames&&fig.frames.length>0;"
        "return {display: has?'flex':'none', alignItems:'center', gap:'8px',"
        "padding:'4px 10px 2px', background:'#fff', borderBottom:'1px solid #e3e6ee'};}",
        Output("anim-bar", "style"),
        Input("trajectory-plot", "figure"),
    )
    
    
    

    return {
        name: value
        for name, value in locals().items()
        if callable(value)
        and name not in {"register_callbacks", "_ContextProxy"}
    }
