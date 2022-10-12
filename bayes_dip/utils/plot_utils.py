"""
Utilities for plotting.
"""
import numpy as np
import matplotlib
import matplotlib.pyplot as plt
from mpl_toolkits.axes_grid1.axes_divider import make_axes_locatable
from mpl_toolkits.axes_grid1.inset_locator import InsetPosition

def configure_matplotlib():
    """
    Configure common matplotlib settings that should be shared by plotting script.
    """
    matplotlib.rc('text', usetex=True)
    matplotlib.rc('text.latex', preamble='\\usepackage{amsmath}')

def hex_to_rgb(value, alpha):
    """
    Convert a hex color string to a 4-tuple of float.
    """
    value = value.lstrip('#')
    lv = len(value)
    out = tuple(int(value[i:i + lv // 3], 16) for i in range(0, lv, lv // 3))
    out = [el / 255 for el in out] + [alpha]
    return tuple(out)

def plot_hist(  # pylint: disable=too-many-arguments
        data, label_list, title=None, ax=None, xlim=None, ylim=None, yscale='log',
        remove_ticks=False, color_list=None, alpha_list=None, hist_kwargs=None,
        hist_kwargs_per_data=None, legend_kwargs=None):
    """
    Plot a set of histograms.

    Returns
    -------
    ax : :class:`matplotlib.axes.Axes`
        Matplotlib axes.
    n_list : list of array
        For each element in ``data``, the ``n`` array as returned by :func:`matplotlib.pyplot.hist`.
    bins_list : list of array
        For each element in ``data``, the ``bins`` array as returned by
        :func:`matplotlib.pyplot.hist`.
    """
    # pylint: disable=too-many-locals
    if ax is None:
        _, ax = plt.subplots()
    if color_list is None:
        color_list = plt.rcParams['axes.prop_cycle'].by_key()['color']
    assert len(color_list) >= len(data)
    if alpha_list is None:
        alpha_list = [0.3] * len(data)
    assert len(alpha_list) >= len(data)
    hist_kwargs = hist_kwargs or {}
    hist_kwargs.setdefault('histtype', 'stepfilled')
    hist_kwargs.setdefault('bins', 25)
    hist_kwargs.setdefault('linewidth', 0.75)
    hist_kwargs.setdefault('alpha', 0.3)
    hist_kwargs.setdefault('linestyle', 'dashed')
    hist_kwargs.setdefault('density', True)
    hist_kwargs_per_data = hist_kwargs_per_data or {}
    hist_kwargs_per_data.setdefault('label', label_list)
    hist_kwargs_per_data.setdefault('zorder', range(3, 3 + len(data)))
    hist_kwargs_per_data.setdefault('facecolor',
            [hex_to_rgb(color, alpha=alpha) for color, alpha in zip(color_list, alpha_list)])
    hist_kwargs_per_data.setdefault('edgecolor',
            [hex_to_rgb(color, alpha=1) for color in color_list])
    assert all(len(v) >= len(data) for v in hist_kwargs_per_data.values())
    hist_kwargs_per_data_list = [
            dict(zip(hist_kwargs_per_data.keys(), v)) for v in zip(*hist_kwargs_per_data.values())]
    n_list = []
    bins_list = []
    for (el, hist_kwargs_overrides) in zip(data, hist_kwargs_per_data_list):
        hist_kwargs_merged = hist_kwargs.copy()
        hist_kwargs_merged.update(hist_kwargs_overrides)
        n, bins, _ = ax.hist(el.flatten(), **hist_kwargs_merged)
        n_list.append(n)
        bins_list.append(bins)
    ax.set_title(title)
    ax.set_xlim(xlim)
    ax.set_ylim(ylim)
    ax.grid(alpha=0.3)
    ax.legend(**(legend_kwargs or {}))
    ax.set_yscale(yscale)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    ax.set_ylabel('density', labelpad=2)
    ax.tick_params(labelbottom=not remove_ticks)
    return ax, n_list, bins_list

def plot_image(
        fig, ax, image,
        title='', vmin=None, vmax=None, cmap='gray', interpolation='none',
        insets=None, insets_mark_in_orig=False, colorbar=False):
    """
    Show an image.

    A colorbar and insets can be added.

    Returns
    -------
    im : :class:`matplotlib.image.AxesImage`
        The object returned by ``ax.imshow(...)``.
    """
    im = ax.imshow(image, vmin=vmin, vmax=vmax, cmap=cmap, interpolation=interpolation)
    ax.set_title(title)
    if insets:
        for inset_spec in insets:
            add_inset(fig, ax, image, **inset_spec, vmin=vmin, vmax=vmax, cmap=cmap, mark_in_orig=insets_mark_in_orig)
    if colorbar:
        cb = add_colorbar(fig, ax, im)
        if colorbar == 'invisible':
            cb.ax.set_visible(False)
    ax.set_xticks([])
    ax.set_yticks([])
    return im

def add_colorbar(fig, ax, im):
    """
    Add a colorbar using ``mpl_toolkits.axes_grid1.axes_divider.make_axes_locatable``.

    Returns
    -------
    cb : :class:`matplotlib.colorbar.Colorbar`
        Colorbar.
    """
    ax_divider = make_axes_locatable(ax)
    cax = ax_divider.append_axes('right', size='4%', pad='2%')
    cb = fig.colorbar(im, cax=cax)
    cax.yaxis.set_major_locator(matplotlib.ticker.MaxNLocator(4))
    return cb

def add_inset(
        fig, ax, image, axes_rect, rect,
        cmap='gray', vmin=None, vmax=None, interpolation='none',
        frame_color='#aa0000', frame_path=None, clip_path_closing=None, mark_in_orig=False,
        origin='upper'):
    """
    Add an inset to an image plot.

    Returns
    -------
    axins : :class:`matplotlib.axes.Axes`
        Inset axes.
    """
    ip = InsetPosition(ax, axes_rect)
    axins = matplotlib.axes.Axes(fig, [0., 0., 1., 1.])
    axins.set_axes_locator(ip)
    fig.add_axes(axins)
    slice0 = slice(rect[0], rect[0]+rect[2])
    slice1 = slice(rect[1], rect[1]+rect[3])
    inset_image = image[slice0, slice1]
    inset_image_handle = axins.imshow(
            inset_image, cmap=cmap, vmin=vmin, vmax=vmax, interpolation=interpolation)
    axins.set_xticks([])
    axins.set_yticks([])
    axins.patch.set_visible(False)
    for spine in axins.spines.values():
        spine.set_visible(False)
    if frame_path is None:
        frame_path = [[0., 0.], [1., 0.], [0., 1.], [1., 1]]
    if frame_path:
        frame_path_closed = frame_path + (clip_path_closing if clip_path_closing is not None else [])
        if mark_in_orig:
            scalex, scaley = rect[3], rect[2]
            offsetx, offsety = rect[1], (image.shape[0]-(rect[0]+rect[2]) if origin == 'upper' else rect[0])
            y_trans = matplotlib.transforms.Affine2D().scale(1., -1.).translate(0., image.shape[0]-1) if origin == 'upper' else matplotlib.transforms.IdentityTransform()
            trans_data = matplotlib.transforms.Affine2D().scale(scalex, scaley).translate(offsetx, offsety) + y_trans + ax.transData
            x, y = [*zip(*(frame_path_closed + [frame_path_closed[0]]))]
            ax.plot(x, y, transform=trans_data, color=frame_color, linestyle='dashed', linewidth=1.)
        axins.plot(
                *np.array(frame_path).T,
                transform=axins.transAxes,
                color=frame_color,
                solid_capstyle='butt')
        inset_image_handle.set_clip_path(matplotlib.path.Path(frame_path_closed),
                transform=axins.transAxes)
        inset_image_handle.set_clip_on(True)
    return axins
